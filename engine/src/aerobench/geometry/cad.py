"""CAD import (STEP, IGES, BREP) by tessellation.

Two back-ends, tried in order:

1. **OpenCASCADE via OCP** (``pip install cadquery-ocp``; LGPL-2.1) — in-process.  Each CAD
   solid (or, for surface models, each shell/face group) becomes a named region, so wheels,
   mirrors or wings modelled as separate bodies arrive as separate regions.
2. **Gmsh executable** (separate GPL program, invoked as an external process, never linked)
   — configured with ``AEROBENCH_GMSH`` or found on ``PATH``.

If neither is available the user gets an actionable message (install the CAD extra, install
Gmsh, or export STL from the CAD system).  Tessellation tolerances are relative to the model
size; the resulting surface goes through the same diagnostics as any imported mesh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from .trisurface import TriSurface

LINEAR_DEFLECTION_REL = 5e-4  # of the bounding-box diagonal
ANGULAR_DEFLECTION = 0.25  # rad


def have_ocp() -> bool:
    try:
        import OCP  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def find_gmsh() -> Optional[str]:
    exe = os.environ.get("AEROBENCH_GMSH")
    if exe and Path(exe).exists():
        return exe
    return shutil.which("gmsh")


def available_backends() -> list[str]:
    out = []
    if have_ocp():
        out.append("opencascade")
    if find_gmsh():
        out.append("gmsh")
    return out


def tessellate_cad(path: Path, linear_rel: float = LINEAR_DEFLECTION_REL, angular: float = ANGULAR_DEFLECTION) -> TriSurface:
    path = Path(path)
    if have_ocp():
        return _tessellate_ocp(path, linear_rel, angular)
    g = find_gmsh()
    if g:
        return _tessellate_gmsh(path, g, linear_rel)
    raise AeroBenchError.make(
        "geometry.cad_backend_missing", ErrorCategory.GEOMETRY,
        f"'{path.name}' is a CAD file, and no CAD tessellation component is installed.",
        likely_cause="STEP/IGES must be converted to a triangulated surface before meshing.",
        action="Install the CAD component (pip install cadquery-ocp in the engine environment), install Gmsh and set AEROBENCH_GMSH, "
               "or export a fine STL/OBJ from your CAD system.",
    )


# ---------------------------------------------------------------------------- OpenCASCADE


def _read_shape(path: Path):
    suf = path.suffix.lower()
    if suf in (".step", ".stp"):
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader

        r = STEPControl_Reader()
        if r.ReadFile(str(path)) != IFSelect_RetDone:
            raise ValueError("the STEP reader could not parse the file")
        r.TransferRoots()
        return r.OneShape()
    if suf in (".iges", ".igs"):
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.IGESControl import IGESControl_Reader

        r = IGESControl_Reader()
        if r.ReadFile(str(path)) != IFSelect_RetDone:
            raise ValueError("the IGES reader could not parse the file")
        r.TransferRoots()
        return r.OneShape()
    if suf in (".brep", ".brp"):
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape

        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
            raise ValueError("the BREP reader could not parse the file")
        return shape
    raise ValueError(f"unsupported CAD format {suf}")


def _tessellate_ocp(path: Path, linear_rel: float, angular: float) -> TriSurface:
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    try:
        shape = _read_shape(path)
    except Exception as exc:  # noqa: BLE001
        raise AeroBenchError.make("geometry.read_failed", ErrorCategory.GEOMETRY, f"Could not read '{path.name}': {exc}",
                                  action="Re-export the CAD file (AP214/AP242 STEP) or export STL.") from exc
    if shape is None or shape.IsNull():
        raise AeroBenchError.make("geometry.read_failed", ErrorCategory.GEOMETRY, f"'{path.name}' contains no geometry.")
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    lo, hi = box.CornerMin(), box.CornerMax()
    diag = float(np.linalg.norm([hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()])) or 1.0
    BRepMesh_IncrementalMesh(shape, linear_rel * diag, False, angular, True)

    to_face = getattr(TopoDS, "Face_s", None) or TopoDS.Face  # OCP < 8 / >= 8
    # group faces by solid (or shell for surface models); faces outside both are "surfaces".
    # Face identity: OCP hash (TShape + location) confirmed with IsSame.
    seen_faces: dict[int, list] = {}

    def first_time(face) -> bool:
        h = face.HashCode(2**31 - 1) if hasattr(face, "HashCode") else hash(face)
        bucket = seen_faces.setdefault(h, [])
        if any(face.IsSame(x) for x in bucket):
            return False
        bucket.append(face)
        return True

    groups: list[tuple[str, list]] = []
    for kind, label in ((TopAbs_SOLID, "solid"), (TopAbs_SHELL, "shell"), (None, "surfaces")):
        ex = TopExp_Explorer(shape, kind) if kind is not None else None
        subs = []
        if ex is None:
            subs = [shape]
        else:
            while ex.More():
                subs.append(ex.Current())
                ex.Next()
        for i, sub in enumerate(subs, start=1):
            faces = []
            fx = TopExp_Explorer(sub, TopAbs_FACE)
            while fx.More():
                if first_time(fx.Current()):
                    faces.append(to_face(fx.Current()))
                fx.Next()
            if faces:
                groups.append((f"{label}_{i}" if kind is not None else label, faces))

    pts_all, faces_all, regs_all, names = [], [], [], []
    offset = 0
    for gi, (name, faces) in enumerate(groups):
        names.append(name)
        for f in faces:
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(f, loc)
            if tri is None:
                continue
            trsf = loc.Transformation()
            n = tri.NbNodes()
            P = np.empty((n, 3))
            for k in range(1, n + 1):
                p = tri.Node(k).Transformed(trsf)
                P[k - 1] = (p.X(), p.Y(), p.Z())
            m = tri.NbTriangles()
            F = np.empty((m, 3), dtype=np.int64)
            for k in range(1, m + 1):
                a, b, c = tri.Triangle(k).Get()
                F[k - 1] = (a - 1, b - 1, c - 1)
            if f.Orientation() == TopAbs_REVERSED:
                F = F[:, ::-1]
            pts_all.append(P)
            faces_all.append(F + offset)
            regs_all.append(np.full(m, gi, dtype=np.int32))
            offset += n
    if not faces_all:
        raise AeroBenchError.make("geometry.read_failed", ErrorCategory.GEOMETRY, f"'{path.name}' produced no triangles.",
                                  action="Check that the file contains surfaces or solids (not only curves).")
    surf = TriSurface(np.vstack(pts_all), np.vstack(faces_all), np.concatenate(regs_all), names)
    # CAD faces share edges but not vertices: weld coincident nodes so the surface is watertight
    from .repair import merge_vertices

    welded, _ = merge_vertices(surf, tolerance=1e-7 * diag)
    return welded


# ---------------------------------------------------------------------------- Gmsh (external process)


def _tessellate_gmsh(path: Path, exe: str, linear_rel: float) -> TriSurface:
    from .io import read_stl

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "tess.stl"
        cmd = [exe, str(path), "-2", "-format", "stl", "-bin", "0", "-o", str(out), "-string",
               "Mesh.StlOneSolidPerSurface=1; Mesh.MeshSizeFromCurvature=24; Mesh.Algorithm=6;"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired as exc:
            raise AeroBenchError.make("geometry.cad_timeout", ErrorCategory.GEOMETRY, "Gmsh did not finish tessellating within 30 minutes.") from exc
        if r.returncode != 0 or not out.exists():
            raise AeroBenchError.make("geometry.read_failed", ErrorCategory.GEOMETRY, f"Gmsh could not tessellate '{path.name}'.",
                                      details=(r.stderr or r.stdout)[-4000:])
        return read_stl(out)
