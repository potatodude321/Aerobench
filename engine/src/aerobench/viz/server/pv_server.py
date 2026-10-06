"""AeroBench ParaView sidecar — runs under ParaView's own ``pvpython`` (ADR-0003).

Protocol: one JSON request per line on stdin; one response per line on stdout, prefixed
with ``@@AB@@`` (ParaView/VTK may print warnings to stdout; unprefixed lines are ignored by
the client).  This script is standalone: it imports only ParaView, VTK and numpy.

Requests: {"id": n, "op": "...", "params": {...}}
Responses: {"id": n, "ok": true, "result": {...}} | {"id": n, "ok": false, "error": "...", "traceback": "..."}
"""

import json
import math
import os
import struct
import sys
import traceback

PREFIX = "@@AB@@"
# pvpython replaces sys.stdin/stdout with VTK capture helpers: use the real file descriptors.
_out = sys.__stdout__ if sys.__stdout__ is not None else os.fdopen(1, "w")
_in = sys.__stdin__ if sys.__stdin__ is not None else os.fdopen(0, "r")


def reply(obj):
    _out.write(PREFIX + json.dumps(obj) + "\n")
    _out.flush()


# Silence stdout from ParaView itself: route print() to stderr.
sys.stdout = sys.stderr

from paraview import servermanager as sm  # noqa: E402
from paraview.simple import (  # noqa: E402
    Calculator,
    CellDatatoPointData,
    Clip,
    ColorBy,
    Contour,
    CreateRenderView,
    Decimate,
    Delete,
    ExtractSurface,
    GetColorTransferFunction,
    Gradient,
    MergeBlocks,
    OpenFOAMReader,
    PlotOverLine,
    ProbeLocation,
    SaveScreenshot,
    Show,
    Slice,
    StreamTracer,
    Triangulate,
)
import numpy as np  # noqa: E402
from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: E402

MAGIC = b"ABG1"


# ------------------------------------------------------------------------------ payload writer


def _pad4(b):
    return b + b"\0" * ((-len(b)) % 4)


def write_abg(path, positions, indices=None, lines=None, scalars=None, vectors=None, part_ids=None, part_names=None, meta=None):
    blocks, data = [], []

    def add(name, arr, dtype):
        a = np.ascontiguousarray(arr, dtype=dtype).reshape(-1)
        blocks.append({"name": name, "dtype": np.dtype(dtype).name, "count": int(a.size)})
        data.append(_pad4(a.tobytes()))

    add("positions", positions, "<f4")
    if indices is not None:
        add("indices", indices, "<u4")
    if lines is not None:
        add("lines", lines, "<u4")
    if scalars is not None:
        add("scalars", scalars, "<f4")
    if vectors is not None:
        add("vectors", vectors, "<f4")
    if part_ids is not None:
        add("part_ids", part_ids, "<u2")
    header = {"blocks": blocks, "n_points": int(np.asarray(positions).reshape(-1, 3).shape[0])}
    if part_names is not None:
        header["part_names"] = list(part_names)
    if scalars is not None and len(scalars):
        s = np.asarray(scalars, float)
        fin = s[np.isfinite(s)]
        header["scalar_range"] = [float(fin.min()), float(fin.max())] if fin.size else [0.0, 0.0]
    if meta:
        header.update(meta)
    hj = json.dumps(header).encode()
    hj = hj + b" " * ((-len(hj)) % 4)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(MAGIC + struct.pack("<I", len(hj)) + hj + b"".join(data))
    os.replace(tmp, path)
    return header


# ------------------------------------------------------------------------------ session state


class Session:
    """Two readers on the same case: one for boundary patches, one for the internal mesh.

    Keeping them separate means a surface request never changes what a volume filter sees
    (and vice versa) — important for multi-item scenes and cached pipelines."""

    def __init__(self):
        self.surf_reader = None
        self.vol_reader = None
        self.case = None
        self.time = None
        self.u_inf = None
        self.l_ref = 1.0
        self._patches = None

    def _new_reader(self, foam_file):
        r = OpenFOAMReader(FileName=foam_file)
        r.UpdatePipelineInformation()
        r.Createcelltopointfiltereddata = 1
        return r

    def open(self, foam_file):
        for r in (self.surf_reader, self.vol_reader):
            if r is not None:
                Delete(r)
        self.case = foam_file
        self.surf_reader = self._new_reader(foam_file)
        self.vol_reader = self._new_reader(foam_file)
        self.vol_reader.MeshRegions = ["internalMesh"]
        self._patches = None
        r = self.surf_reader
        times = list(r.TimestepValues) if r.TimestepValues is not None else []
        if not isinstance(times, list):
            times = [times]
        self.time = times[-1] if times else 0.0
        return {
            "times": times,
            "regions": list(r.GetProperty("MeshRegions").Available),
            "cell_arrays": list(r.CellArrays.Available),
            "point_arrays": list(r.PointArrays.Available) if hasattr(r, "PointArrays") else [],
        }

    def surfaces(self, patches):
        regions = ["patch/" + p for p in patches]
        if regions != self._patches:
            self.surf_reader.MeshRegions = regions
            self._patches = regions
        return self.surf_reader

    def internal(self):
        return self.vol_reader


S = Session()


# ------------------------------------------------------------------------------ helpers


def _fetch_polydata(src):
    src.UpdatePipeline(S.time)
    obj = sm.Fetch(src)
    return obj


def _merge_tri(src, decimate_target=None):
    """Return (points, triangles, polydata) after surface extraction + triangulation."""
    surf = ExtractSurface(Input=MergeBlocks(Input=src))
    tri = Triangulate(Input=surf)
    last = tri
    pd = _fetch_polydata(tri)
    n = pd.GetNumberOfCells()
    dec_ratio = 0.0
    if decimate_target and n > decimate_target:
        dec_ratio = min(0.95, 1.0 - decimate_target / float(n))
        last = Decimate(Input=tri, TargetReduction=dec_ratio, PreserveTopology=0)
        pd = _fetch_polydata(last)
    pts = vtk_to_numpy(pd.GetPoints().GetData()) if pd.GetPoints() else np.zeros((0, 3))
    polys = pd.GetPolys()
    conn = vtk_to_numpy(polys.GetConnectivityArray()) if polys.GetNumberOfCells() else np.zeros(0, int)
    return pts, conn.reshape(-1, 3), pd, dec_ratio


def _array(pd, name, component="Magnitude", where="point"):
    data = pd.GetPointData() if where == "point" else pd.GetCellData()
    arr = data.GetArray(name)
    if arr is None:
        return None, None
    a = vtk_to_numpy(arr).astype(float)
    if a.ndim == 2:
        vec = a
        if component in ("Magnitude", None):
            a = np.linalg.norm(a, axis=1)
        else:
            a = a[:, {"X": 0, "Y": 1, "Z": 2}[component]]
        return a, vec
    return a, None


def _derived(src, field):
    """Fields computed on the fly: Cp (if not written), Umag/Uinf, Q."""
    if field == "Cp" and "Cp" not in list(S.surf_reader.CellArrays.Available):
        if not S.u_inf:
            raise RuntimeError("Cp needs the freestream speed (open_case u_inf)")
        calc = Calculator(Input=src, ResultArrayName="Cp", Function="p/(0.5*%r)" % (S.u_inf ** 2))
        return calc, "Cp"
    if field == "U_ratio":
        calc = Calculator(Input=src, ResultArrayName="U_ratio", Function="mag(U)/%r" % (S.u_inf or 1.0))
        return calc, "U_ratio"
    return src, field


def _vehicle_regions(patches):
    return ["patch/" + p for p in patches]


# ------------------------------------------------------------------------------ operations


def op_ping(p):
    return {"version": [sm.vtkSMProxyManager.GetVersionMajor(), sm.vtkSMProxyManager.GetVersionMinor(),
                        sm.vtkSMProxyManager.GetVersionPatch()], "python": sys.version.split()[0], "pid": os.getpid()}


def op_open_case(p):
    info = S.open(p["foam_file"])
    S.u_inf = p.get("u_inf")
    S.l_ref = p.get("l_ref", 1.0)
    if p.get("time") not in (None, "latest") and info["times"]:
        S.time = min(info["times"], key=lambda t: abs(t - float(p["time"])))
    info["time"] = S.time
    return info


def _surface_extract(patches, field, component, decimate):
    src, fname = _derived(S.surfaces(patches), field)
    pts, tri, pd, ratio = _merge_tri(src, decimate)
    vals, vec = _array(pd, fname, component)
    return pts, tri, pd, ratio, vals, vec


def op_surface(p):
    patches = p["patches"]
    parts_pts, parts_tri, parts_val, part_ids = [], [], [], []
    off = 0
    ratio_max = 0.0
    names = []
    per_part = p.get("per_part", True)
    groups = [[x] for x in patches] if per_part else [patches]
    for gi, grp in enumerate(groups):
        pts, tri, pd, ratio, vals, _ = _surface_extract(grp, p["field"], p.get("component", "Magnitude"),
                                                      max(2000, int(p.get("max_faces", 400000) / max(1, len(groups)))))
        if len(tri) == 0:
            continue
        ratio_max = max(ratio_max, ratio)
        parts_pts.append(pts)
        parts_tri.append(tri + off)
        off += len(pts)
        parts_val.append(vals if vals is not None else np.full(len(pts), np.nan))
        part_ids.append(np.full(len(tri), len(names), dtype=np.uint16))
        names.append("+".join(grp))
    if not parts_pts:
        raise RuntimeError("no surface data for the requested patches")
    P = np.vstack(parts_pts)
    T = np.vstack(parts_tri)
    V = np.concatenate(parts_val)
    hdr = write_abg(p["out"], P, indices=T, scalars=V, part_ids=np.concatenate(part_ids), part_names=names,
                    meta={"kind": "surface", "field": p["field"], "component": p.get("component", "Magnitude"),
                          "time": S.time, "decimation": ratio_max})
    return hdr


def _internal():
    return S.internal()


def op_slice(p):
    src = _internal()
    src, fname = _derived(src, p["field"])
    sl = Slice(Input=src)
    sl.SliceType = "Plane"
    sl.SliceType.Origin = p["origin"]
    sl.SliceType.Normal = p["normal"]
    sl.Triangulatetheslice = 1
    last = sl
    if p.get("clip_box"):
        lo, hi = p["clip_box"]
        cl = Clip(Input=sl)
        cl.ClipType = "Box"
        cl.ClipType.Position = lo
        cl.ClipType.Length = [hi[i] - lo[i] for i in range(3)]
        cl.Invert = 1
        last = cl
    pts, tri, pd, ratio = _merge_tri(last, p.get("max_faces", 300000))
    vals, vec = _array(pd, fname, p.get("component", "Magnitude"))
    uvals, uvec = _array(pd, "U", "Magnitude") if p.get("with_vectors") else (None, None)
    hdr = write_abg(p["out"], pts, indices=tri, scalars=vals, vectors=uvec,
                    meta={"kind": "slice", "field": p["field"], "component": p.get("component", "Magnitude"), "time": S.time,
                          "origin": p["origin"], "normal": p["normal"], "decimation": ratio})
    return hdr


def op_streamlines(p):
    src = _internal()
    st = StreamTracer(Input=src, SeedType="Line")
    st.Vectors = ["POINTS", "U"]
    st.SeedType.Point1 = p["p1"]
    st.SeedType.Point2 = p["p2"]
    st.SeedType.Resolution = int(p.get("resolution", 60))
    st.MaximumStreamlineLength = float(p.get("max_length", 20.0))
    st.IntegrationDirection = p.get("direction", "BOTH")
    src2, fname = _derived(st, p.get("field", "U"))
    pd = _fetch_polydata(src2)
    if pd.GetNumberOfPoints() == 0:
        raise RuntimeError("no streamlines were generated (seeds outside the flow?)")
    pts = vtk_to_numpy(pd.GetPoints().GetData())
    lines = pd.GetLines()
    offs = vtk_to_numpy(lines.GetOffsetsArray())
    conn = vtk_to_numpy(lines.GetConnectivityArray())
    packed = []
    for i in range(len(offs) - 1):
        seg = conn[offs[i]:offs[i + 1]]
        if len(seg) >= 2:
            packed.append(len(seg))
            packed.extend(seg.tolist())
    vals, _ = _array(pd, fname, p.get("component", "Magnitude"))
    return write_abg(p["out"], pts, lines=np.asarray(packed, dtype=np.uint32), scalars=vals,
                     meta={"kind": "streamlines", "field": p.get("field", "U"), "time": S.time, "n_lines": int(len(offs) - 1)})


def op_isosurface(p):
    src = _internal()
    field = p["field"]
    if field == "Q":
        g = Gradient(Input=src)
        g.ScalarArray = ["POINTS", "U"]
        g.ComputeGradient = 0
        g.ComputeQCriterion = 1
        g.QCriterionArrayName = "Q"
        src = g
        scale = (S.u_inf or 1.0) ** 2 / (S.l_ref or 1.0) ** 2  # Q value given nondimensionally: Q* = Q (L/U)^2
        values = [v * scale for v in p["values"]]
    else:
        values = p["values"]
    c = Contour(Input=src)
    c.ContourBy = ["POINTS", field]
    c.Isosurfaces = values
    c.ComputeScalars = 1
    color_src, cname = _derived(c, p.get("color_field", "U"))
    pts, tri, pd, ratio = _merge_tri(color_src, p.get("max_faces", 300000))
    vals, _ = _array(pd, cname, p.get("component", "Magnitude"))
    return write_abg(p["out"], pts, indices=tri, scalars=vals,
                     meta={"kind": "isosurface", "field": field, "values": p["values"], "color_field": p.get("color_field", "U"),
                           "time": S.time, "decimation": ratio})


def op_line_sample(p):
    src = _internal()
    pol = PlotOverLine(Input=src)
    pol.Point1 = p["p1"]
    pol.Point2 = p["p2"]
    pol.Resolution = int(p.get("resolution", 200))
    pd = _fetch_polydata(pol)
    out = {"distance": vtk_to_numpy(pd.GetPointData().GetArray("arc_length")).tolist()}
    for f in p.get("fields", ["p", "U"]):
        vals, vec = _array(pd, f, "Magnitude")
        if vals is not None:
            out[f] = [None if not math.isfinite(x) else float(x) for x in vals]
    valid = pd.GetPointData().GetArray("vtkValidPointMask")
    if valid is not None:
        out["valid"] = vtk_to_numpy(valid).astype(int).tolist()
    return out


def op_probe(p):
    src = _internal()
    res = []
    for pt in p["points"]:
        pr = ProbeLocation(Input=src, ProbeType="Fixed Radius Point Source")
        pr.ProbeType.Center = pt
        pd = _fetch_polydata(pr)
        row = {"point": pt}
        for f in p.get("fields", ["p", "U"]):
            arr = pd.GetPointData().GetArray(f)
            if arr is not None and arr.GetNumberOfTuples():
                v = vtk_to_numpy(arr)[0]
                row[f] = v.tolist() if hasattr(v, "tolist") else float(v)
        res.append(row)
        Delete(pr)
    return {"probes": res}


def op_screenshot(p):
    """Render a scene with ParaView (reports / thumbnails)."""
    view = CreateRenderView()
    view.ViewSize = p.get("size", [1600, 900])
    view.OrientationAxesVisibility = 0
    view.Background = p.get("background", [1, 1, 1])
    try:
        view.UseColorPaletteForBackground = 0
    except AttributeError:
        pass
    for item in p["items"]:
        kind = item["kind"]
        if kind == "surface":
            # dedicated reader per surface item so several surface items can coexist
            rd = S._new_reader(S.case)
            rd.MeshRegions = ["patch/" + x for x in item["patches"]]
            src, fname = _derived(rd, item["field"])
            src = MergeBlocks(Input=src)
        elif kind == "slice":
            src0, fname = _derived(_internal(), item["field"])
            src = Slice(Input=src0)
            src.SliceType = "Plane"
            src.SliceType.Origin = item["origin"]
            src.SliceType.Normal = item["normal"]
            if item.get("clip_box"):
                lo, hi = item["clip_box"]
                cl = Clip(Input=src)
                cl.ClipType = "Box"
                cl.ClipType.Position = lo
                cl.ClipType.Length = [hi[i] - lo[i] for i in range(3)]
                cl.Invert = 1
                src = cl
        else:
            continue
        disp = Show(src, view)
        ColorBy(disp, ("POINTS", fname, item.get("component", "Magnitude")))
        lut = GetColorTransferFunction(fname)
        lut.ApplyPreset(item.get("colormap", "Cool to Warm"), True)
        if item.get("range"):
            lut.RescaleTransferFunction(*item["range"])
        if item.get("clip_box"):
            pass  # clipping is applied upstream for slices (see below)
        disp.SetScalarBarVisibility(view, bool(item.get("scalar_bar", True)))
        if item.get("scalar_bar", True):
            from paraview.simple import GetScalarBar

            bar = GetScalarBar(lut, view)
            bar.Title = item.get("title", fname)
            bar.ComponentTitle = ""
            dark = sum(p.get("background", [1, 1, 1])) < 1.5
            col = [1, 1, 1] if dark else [0.1, 0.1, 0.1]
            bar.TitleColor = col
            bar.LabelColor = col
            bar.TitleFontSize = 14
            bar.LabelFontSize = 12
    cam = p.get("camera")
    view.ResetCamera()
    if cam:
        c = view.GetActiveCamera()
        c.SetPosition(*cam["position"])
        c.SetFocalPoint(*cam["focal_point"])
        c.SetViewUp(*cam["view_up"])
        if cam.get("parallel_scale"):
            view.CameraParallelProjection = 1
            view.CameraParallelScale = cam["parallel_scale"]
        view.ResetCamera(False) if cam.get("fit", True) else None
    SaveScreenshot(p["out"], view, ImageResolution=p.get("size", [1600, 900]))
    Delete(view)
    return {"path": p["out"]}


OPS = {
    "ping": op_ping,
    "open_case": op_open_case,
    "surface": op_surface,
    "slice": op_slice,
    "streamlines": op_streamlines,
    "isosurface": op_isosurface,
    "line_sample": op_line_sample,
    "probe": op_probe,
    "screenshot": op_screenshot,
}


def main():
    reply({"id": 0, "ok": True, "result": {"ready": True}})
    while True:
        line = _in.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid = req.get("id")
        op = req.get("op")
        if op == "shutdown":
            reply({"id": rid, "ok": True, "result": {}})
            break
        fn = OPS.get(op)
        if fn is None:
            reply({"id": rid, "ok": False, "error": "unknown op %s" % op})
            continue
        try:
            reply({"id": rid, "ok": True, "result": fn(req.get("params") or {})})
        except Exception as exc:  # noqa: BLE001
            reply({"id": rid, "ok": False, "error": str(exc), "traceback": traceback.format_exc()})


if __name__ == "__main__":
    main()
