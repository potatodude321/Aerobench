"""Surface import/export: STL (ASCII multi-solid and binary), OBJ (groups), PLY.

CAD formats (STEP/IGES/BRep) go through :mod:`aerobench.geometry.cad` which tessellates
with a replaceable external backend.
"""

from __future__ import annotations

import io
import re
import struct
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from .fastops import weld_float_rows
from .trisurface import TriSurface

SUPPORTED_MESH_SUFFIXES = {".stl", ".obj", ".ply"}
CAD_SUFFIXES = {".step", ".stp", ".iges", ".igs", ".brep", ".brp"}


def _geometry_error(msg: str, path: Path, details: str = "") -> AeroBenchError:
    return AeroBenchError.make(
        "geometry.read_failed",
        ErrorCategory.GEOMETRY,
        f"Could not read '{path.name}': {msg}",
        likely_cause="The file is corrupt, truncated or not in the expected format.",
        action="Re-export the geometry from your CAD/mesh tool (binary or ASCII STL, OBJ or PLY).",
        details=details,
    )


def read_surface(path: Path | str) -> TriSurface:
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".stl":
        return read_stl(p)
    if suf == ".obj":
        return read_obj(p)
    if suf == ".ply":
        return read_ply(p)
    if suf in CAD_SUFFIXES:
        from .cad import tessellate_cad

        return tessellate_cad(p)
    raise AeroBenchError.make(
        "geometry.unsupported_format",
        ErrorCategory.GEOMETRY,
        f"Unsupported geometry format '{suf}'.",
        action="Use STL, OBJ, PLY, STEP or IGES.",
    )


# ------------------------------------------------------------------------------------ STL


def _is_binary_stl(data: bytes) -> bool:
    if len(data) < 84:
        return False
    (n,) = struct.unpack_from("<I", data, 80)
    if 84 + 50 * n == len(data):
        return True
    head = data[:1024].lstrip()
    if not head.lower().startswith(b"solid"):
        return True
    # starts with 'solid' but may still be binary: look for ascii keywords
    return b"facet" not in data[:4096] and b"endsolid" not in data[-4096:]


def read_stl(path: Path | str) -> TriSurface:
    p = Path(path)
    data = p.read_bytes()
    try:
        if _is_binary_stl(data):
            return _read_stl_binary(data, p.stem)
        return _read_stl_ascii(data.decode("utf-8", errors="replace"), p.stem)
    except AeroBenchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _geometry_error(str(exc), p) from exc


def _read_stl_binary(data: bytes, name: str) -> TriSurface:
    (n,) = struct.unpack_from("<I", data, 80)
    expected = 84 + 50 * n
    if len(data) < expected:
        raise ValueError(f"binary STL truncated: header says {n} triangles, file has {(len(data) - 84) // 50}")
    dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    arr = np.frombuffer(data, dtype=dt, count=n, offset=84)
    tri = arr["v"].astype(np.float64)
    return _from_triangle_soup(tri, np.zeros(n, np.int32), [name or "solid"])


_VERTEX_RE = re.compile(r"vertex\s+(\S+\s+\S+\s+\S+)")
_SOLID_RE = re.compile(r"^[ \t]*solid[ \t]*([^\r\n]*)$", re.MULTILINE)


def _parse_floats(text: str, expected: int, where: str) -> np.ndarray:
    """Whitespace-separated numbers -> float64 array (C-level parse; exact count enforced)."""
    import warnings

    try:
        with warnings.catch_warnings():
            # older numpy only warns (and returns what it read) when it stops early
            warnings.simplefilter("ignore", DeprecationWarning)
            vals = np.fromstring(text, dtype=np.float64, sep=" ")
    except ValueError:
        vals = np.zeros(0)
    if vals.size != expected:
        try:  # report the offending token
            bad = next(t for t in text.split() if not _is_float(t))
        except StopIteration:
            bad = "?"
        raise ValueError(f"solid '{where}': unreadable vertex coordinate '{bad[:40]}'")
    return vals


def _is_float(t: str) -> bool:
    try:
        float(t)
        return True
    except ValueError:
        return False


def _read_stl_ascii(text: str, default_name: str) -> TriSurface:
    tris, regs, names = [], [], []
    starts = [m for m in _SOLID_RE.finditer(text)]
    if not starts:
        raise ValueError("no 'solid' keyword found")
    for k, m in enumerate(starts):
        name = m.group(1).strip() or f"{default_name}_{k}" if len(starts) > 1 else (m.group(1).strip() or default_name)
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        block = text[m.end() : end]
        coords = _VERTEX_RE.findall(block)
        if len(coords) % 3:
            raise ValueError(f"solid '{name}' has {len(coords)} vertices (not a multiple of 3)")
        if not coords:
            continue
        v = _parse_floats(" ".join(coords), 3 * len(coords), name).reshape(-1, 3, 3)
        if name in names:
            idx = names.index(name)
        else:
            names.append(name)
            idx = len(names) - 1
        tris.append(v)
        regs.append(np.full(len(v), idx, np.int32))
    if not tris:
        raise ValueError("no triangles found")
    return _from_triangle_soup(np.concatenate(tris), np.concatenate(regs), names)


def _from_triangle_soup(tri: np.ndarray, regions: np.ndarray, names: list[str]) -> TriSurface:
    """Build an indexed surface by merging bit-identical vertices."""
    if not np.all(np.isfinite(tri)):
        raise ValueError("non-finite coordinates in geometry")
    flat = tri.reshape(-1, 3)
    uniq, inv = weld_float_rows(flat)
    faces = inv.reshape(-1, 3)
    return TriSurface(uniq, faces, regions, names)


def write_stl(path: Path | str, surf: TriSurface, binary: bool = False, solid_name: Optional[str] = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        n = surf.n_faces
        dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        arr = np.zeros(n, dtype=dt)
        arr["n"] = surf.face_normals().astype(np.float32)
        arr["v"] = surf.triangles().astype(np.float32)
        header = (solid_name or "aerobench").encode()[:80].ljust(80, b" ")
        with open(p, "wb") as fh:
            fh.write(header)
            fh.write(struct.pack("<I", n))
            fh.write(arr.tobytes())
        return
    buf = io.StringIO()
    normals = surf.face_normals()
    tris = surf.triangles()
    for ri, rname in enumerate(surf.region_names):
        mask = surf.regions == ri
        if not mask.any():
            continue
        nm = solid_name if (solid_name and len(surf.region_names) == 1) else rname
        buf.write(f"solid {nm}\n")
        for nrm, t in zip(normals[mask], tris[mask]):
            buf.write(f" facet normal {nrm[0]:.9g} {nrm[1]:.9g} {nrm[2]:.9g}\n  outer loop\n")
            for v in t:
                buf.write(f"   vertex {v[0]:.12g} {v[1]:.12g} {v[2]:.12g}\n")
            buf.write("  endloop\n endfacet\n")
        buf.write(f"endsolid {nm}\n")
    p.write_text(buf.getvalue())


# ------------------------------------------------------------------------------------ OBJ


def read_obj(path: Path | str) -> TriSurface:
    p = Path(path)
    try:
        verts: list[list[float]] = []
        faces: list[list[int]] = []
        regs: list[int] = []
        names: list[str] = []
        cur = -1

        def region(name: str) -> int:
            if name not in names:
                names.append(name)
            return names.index(name)

        for line in p.read_text(errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            tag = parts[0]
            if tag == "v":
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif tag in ("g", "o"):
                cur = region(" ".join(parts[1:]) or p.stem)
            elif tag == "f":
                idx = []
                for tok in parts[1:]:
                    i = int(tok.split("/")[0])
                    idx.append(i - 1 if i > 0 else len(verts) + i)
                if cur < 0:
                    cur = region(p.stem)
                for k in range(1, len(idx) - 1):  # fan triangulation
                    faces.append([idx[0], idx[k], idx[k + 1]])
                    regs.append(cur)
        if not faces:
            raise ValueError("no faces found")
        return TriSurface(np.array(verts), np.array(faces), np.array(regs), names)
    except AeroBenchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _geometry_error(str(exc), p) from exc


def write_obj(path: Path | str, surf: TriSurface) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = io.StringIO()
    for v in surf.points:
        out.write(f"v {v[0]:.12g} {v[1]:.12g} {v[2]:.12g}\n")
    for ri, rname in enumerate(surf.region_names):
        mask = surf.regions == ri
        if not mask.any():
            continue
        out.write(f"g {rname}\n")
        for f in surf.faces[mask] + 1:
            out.write(f"f {f[0]} {f[1]} {f[2]}\n")
    p.write_text(out.getvalue())


# ------------------------------------------------------------------------------------ PLY

_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


def read_ply(path: Path | str) -> TriSurface:
    p = Path(path)
    try:
        data = p.read_bytes()
        end = data.find(b"end_header")
        if not data.startswith(b"ply") or end < 0:
            raise ValueError("not a PLY file")
        nl = data.find(b"\n", end)
        header = data[:nl].decode("ascii", errors="replace").splitlines()
        body = data[nl + 1 :]
        fmt = None
        elements: list[tuple[str, int, list[tuple]]] = []
        for line in header:
            t = line.split()
            if not t:
                continue
            if t[0] == "format":
                fmt = t[1]
            elif t[0] == "element":
                elements.append((t[1], int(t[2]), []))
            elif t[0] == "property":
                if t[1] == "list":
                    elements[-1][2].append(("list", t[2], t[3], t[4]))
                else:
                    elements[-1][2].append(("scalar", t[1], t[2]))
        if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
            raise ValueError(f"unsupported PLY format {fmt}")
        verts = faces = None
        if fmt == "ascii":
            tokens = body.decode("ascii", errors="replace").split()
            pos = 0
            for name, count, props in elements:
                if name == "vertex":
                    nprop = len(props)
                    arr = np.array(tokens[pos : pos + count * nprop], dtype=float).reshape(count, nprop)
                    pos += count * nprop
                    names = [pp[2] for pp in props]
                    verts = arr[:, [names.index("x"), names.index("y"), names.index("z")]]
                elif name == "face":
                    fl = []
                    for _ in range(count):
                        k = int(tokens[pos])
                        idx = [int(x) for x in tokens[pos + 1 : pos + 1 + k]]
                        pos += 1 + k
                        for j in range(1, k - 1):
                            fl.append([idx[0], idx[j], idx[j + 1]])
                    faces = np.array(fl)
                else:
                    pos += count * len(props)
        else:
            end_c = "<" if fmt == "binary_little_endian" else ">"
            off = 0
            for name, count, props in elements:
                if all(pp[0] == "scalar" for pp in props):
                    dt = np.dtype([(pp[2], end_c + _PLY_TYPES[pp[1]]) for pp in props])
                    arr = np.frombuffer(body, dtype=dt, count=count, offset=off)
                    off += dt.itemsize * count
                    if name == "vertex":
                        verts = np.column_stack([arr["x"], arr["y"], arr["z"]]).astype(float)
                else:
                    if name != "face" or len(props) != 1:
                        raise ValueError("unsupported PLY list layout")
                    _, ct, it, _ = props[0]
                    cdt = np.dtype(end_c + _PLY_TYPES[ct])
                    idt = np.dtype(end_c + _PLY_TYPES[it])
                    fl = []
                    for _ in range(count):
                        k = int(np.frombuffer(body, cdt, 1, off)[0])
                        off += cdt.itemsize
                        idx = np.frombuffer(body, idt, k, off).astype(np.int64)
                        off += idt.itemsize * k
                        for j in range(1, k - 1):
                            fl.append([idx[0], idx[j], idx[j + 1]])
                    faces = np.array(fl)
        if verts is None or faces is None or len(faces) == 0:
            raise ValueError("PLY has no vertex/face data")
        return TriSurface(verts, faces, None, [p.stem])
    except AeroBenchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _geometry_error(str(exc), p) from exc
