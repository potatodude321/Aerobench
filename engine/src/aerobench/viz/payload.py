"""Compact binary geometry payload shared by the engine and the ParaView sidecar.

Layout (little endian)::

    magic  b"ABG1"
    uint32 header_length
    header JSON (utf-8), padded with spaces to a multiple of 4 bytes
    blocks in header["blocks"] order, each 4-byte aligned:
        positions float32[n_points*3]
        indices   uint32[n_tri*3]      (triangles)   or
        lines     uint32[...]          (polyline connectivity: count, ids..., count, ids...)
        scalars   float32[n_points]    (optional)
        vectors   float32[n_points*3]  (optional)
        part_ids  uint16[n_tri]        (optional, padded)

The renderer reads it with typed-array views — no parsing in JavaScript.
"""

from __future__ import annotations

import json
import struct
from typing import Optional

import numpy as np

MAGIC = b"ABG1"


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * ((-len(b)) % 4)


def encode_mesh(
    positions: np.ndarray,
    indices: Optional[np.ndarray] = None,
    scalars: Optional[np.ndarray] = None,
    vectors: Optional[np.ndarray] = None,
    lines: Optional[np.ndarray] = None,
    part_ids: Optional[np.ndarray] = None,
    part_names: Optional[list[str]] = None,
    meta: Optional[dict] = None,
) -> tuple[dict, bytes]:
    blocks = []
    data = []

    def add(name, arr, dtype):
        a = np.ascontiguousarray(arr, dtype=dtype)
        blocks.append({"name": name, "dtype": np.dtype(dtype).name, "count": int(a.size)})
        data.append(_pad4(a.tobytes()))

    add("positions", positions.reshape(-1), "<f4")
    if indices is not None:
        add("indices", indices.reshape(-1), "<u4")
    if lines is not None:
        add("lines", lines.reshape(-1), "<u4")
    if scalars is not None:
        add("scalars", scalars.reshape(-1), "<f4")
    if vectors is not None:
        add("vectors", vectors.reshape(-1), "<f4")
    if part_ids is not None:
        add("part_ids", part_ids.reshape(-1), "<u2")
    header = {"blocks": blocks, "n_points": int(positions.reshape(-1, 3).shape[0])}
    if part_names is not None:
        header["part_names"] = list(part_names)
    if scalars is not None:
        s = np.asarray(scalars, float)
        fin = s[np.isfinite(s)]
        header["scalar_range"] = [float(fin.min()), float(fin.max())] if fin.size else [0.0, 0.0]
    if meta:
        header.update(meta)
    return header, b"".join(data)


def pack(header: dict, blob: bytes) -> bytes:
    hj = json.dumps(header).encode()
    hj = hj + b" " * ((-len(hj)) % 4)
    return MAGIC + struct.pack("<I", len(hj)) + hj + blob


def unpack(buf: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    if buf[:4] != MAGIC:
        raise ValueError("not an ABG payload")
    (n,) = struct.unpack_from("<I", buf, 4)
    header = json.loads(buf[8 : 8 + n].decode())
    off = 8 + n
    arrays = {}
    for b in header["blocks"]:
        dt = np.dtype(b["dtype"]).newbyteorder("<")
        size = dt.itemsize * b["count"]
        arrays[b["name"]] = np.frombuffer(buf, dtype=dt, count=b["count"], offset=off)
        off += size + ((-size) % 4)
    return header, arrays


def read_header(path) -> dict:
    """Read only the JSON header of an ABG file."""
    with open(path, "rb") as fh:
        head = fh.read(8)
        if head[:4] != MAGIC:
            raise ValueError("not an ABG payload")
        (n,) = struct.unpack_from("<I", head, 4)
        return json.loads(fh.read(n).decode())
