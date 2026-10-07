"""Projected (frontal) area by rasterisation.

The silhouette of all triangles projected onto the plane normal to ``direction`` is
rasterised at pixel size ``h``; area = filled pixels × h².  The reported uncertainty is
half a pixel along the silhouette perimeter, which bounds the rasterisation error for
well-resolved silhouettes.  Overlapping parts are counted once (union), as required for
a frontal area.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .trisurface import TriSurface


@dataclass
class ProjectedArea:
    area: float
    uncertainty: float
    pixel_size: float
    direction: tuple[float, float, float]
    mask: np.ndarray
    origin: tuple[float, float]  # (u0, v0) of pixel (0, 0) corner

    def as_dict(self, include_mask: bool = False) -> dict:
        d = {"area": self.area, "uncertainty": self.uncertainty, "pixel_size": self.pixel_size, "direction": list(self.direction)}
        if include_mask:
            d["mask_shape"] = list(self.mask.shape)
        return d


def _basis(direction) -> tuple[np.ndarray, np.ndarray]:
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    up = np.array([0.0, 0.0, 1.0])
    if abs(d @ up) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    u = np.cross(up, d)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    return u, v


def projected_area(
    surf: TriSurface,
    direction=(1.0, 0.0, 0.0),
    pixel_size: Optional[float] = None,
    resolution: int = 1200,
    face_mask: Optional[np.ndarray] = None,
) -> ProjectedArea:
    u, v = _basis(direction)
    tri = surf.triangles() if face_mask is None else surf.triangles()[face_mask]
    P = np.stack([tri @ u, tri @ v], axis=-1)  # (M, 3, 2)
    lo = P.reshape(-1, 2).min(axis=0)
    hi = P.reshape(-1, 2).max(axis=0)
    span = hi - lo
    h = pixel_size or float(span.max() / resolution)
    nx = int(np.ceil(span[0] / h)) + 2
    ny = int(np.ceil(span[1] / h)) + 2
    origin = lo - h

    Q = (P - origin) / h  # pixel coordinates; pixel (i,j) centre at (i+0.5, j+0.5)
    a, b, c = Q[:, 0], Q[:, 1], Q[:, 2]
    area2 = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.abs(area2) > 1e-12
    a, b, c = a[keep], b[keep], c[keep]
    # Scanline fill: for every (triangle, pixel row) pair the covered pixel centres form one
    # interval, found from the triangle's edge crossings at the row's centre line. Spans are
    # accumulated with a difference array, so the cost scales with the number of rows a
    # triangle spans, not its pixel area (large panels used to dominate the run time).
    ymin = np.minimum(np.minimum(a[:, 1], b[:, 1]), c[:, 1])
    ymax = np.maximum(np.maximum(a[:, 1], b[:, 1]), c[:, 1])
    j0 = np.ceil(ymin - 0.5).astype(np.int64).clip(0, ny - 1)
    j1 = np.floor(ymax - 0.5).astype(np.int64).clip(-1, ny - 1)
    rows = (j1 - j0 + 1).clip(0)
    cov = np.zeros((nx + 1) * ny, np.int64)
    budget = 8_000_000
    order = np.arange(len(rows))
    csum = np.cumsum(rows)
    start = 0
    while start < len(order):
        # batches of triangles with at most `budget` (triangle, row) pairs
        base = csum[start - 1] if start else 0
        end = int(np.searchsorted(csum, base + budget, side="right"))
        end = max(end, start + 1)
        sel = order[start:end]
        start = end
        rep = rows[sel]
        if rep.sum() == 0:
            continue
        tid = np.repeat(sel, rep)
        local = np.arange(rep.sum()) - np.repeat(np.cumsum(rep) - rep, rep)
        jj = j0[tid] + local
        yc = jj + 0.5
        xlo = np.full(len(tid), np.inf)
        xhi = np.full(len(tid), -np.inf)
        for p, q in ((a, b), (b, c), (c, a)):
            py, qy = p[tid, 1], q[tid, 1]
            px, qx = p[tid, 0], q[tid, 0]
            dy = qy - py
            hit = ((yc - py) * (yc - qy) <= 0) & (dy != 0)
            x = np.where(hit, px + (yc - py) * (qx - px) / np.where(dy != 0, dy, 1.0), np.nan)
            xlo = np.where(hit, np.minimum(xlo, x), xlo)
            xhi = np.where(hit, np.maximum(xhi, x), xhi)
        ok = np.isfinite(xlo) & np.isfinite(xhi)
        i_s = np.ceil(xlo[ok] - 0.5).astype(np.int64).clip(0, nx)
        i_e = np.floor(xhi[ok] - 0.5).astype(np.int64).clip(-1, nx - 1)
        jj = jj[ok]
        good = i_s <= i_e
        i_s, i_e, jj = i_s[good], i_e[good], jj[good]
        cov += np.bincount(i_s * ny + jj, minlength=cov.size)
        cov -= np.bincount((i_e + 1) * ny + jj, minlength=cov.size)
    mask = np.cumsum(cov.reshape(nx + 1, ny), axis=0)[:nx] > 0

    filled = int(mask.sum())
    # perimeter pixels: filled pixels with at least one empty 4-neighbour
    m = mask
    border = m & ~(np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1))
    area = filled * h * h
    unc = 0.5 * int(border.sum()) * h * h
    return ProjectedArea(area, unc, h, tuple(float(x) for x in np.asarray(direction) / np.linalg.norm(direction)), mask, (float(origin[0]), float(origin[1])))
