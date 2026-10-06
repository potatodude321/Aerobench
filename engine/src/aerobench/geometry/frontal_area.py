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
    mask = np.zeros((nx, ny), bool)

    Q = (P - origin) / h  # pixel coordinates; pixel (i,j) centre at (i+0.5, j+0.5)
    a, b, c = Q[:, 0], Q[:, 1], Q[:, 2]
    area2 = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.abs(area2) > 1e-12
    a, b, c = a[keep], b[keep], c[keep]
    i0 = np.floor(np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0]) - 0.5).astype(int).clip(0, nx - 1)
    i1 = np.ceil(np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0]) - 0.5).astype(int).clip(0, nx - 1)
    j0 = np.floor(np.minimum(np.minimum(a[:, 1], b[:, 1]), c[:, 1]) - 0.5).astype(int).clip(0, ny - 1)
    j1 = np.ceil(np.maximum(np.maximum(a[:, 1], b[:, 1]), c[:, 1]) - 0.5).astype(int).clip(0, ny - 1)
    wi = i1 - i0 + 1
    wj = j1 - j0 + 1
    cnt = wi * wj

    def edge(p, q, x, y):
        return (q[..., 0] - p[..., 0]) * (y - p[..., 1]) - (q[..., 1] - p[..., 1]) * (x - p[..., 0])

    # process in batches by expanding candidate pixels
    order = np.argsort(cnt)
    start = 0
    budget = 4_000_000
    while start < len(order):
        tot = 0
        end = start
        while end < len(order) and (tot + cnt[order[end]] <= budget or end == start):
            tot += cnt[order[end]]
            end += 1
        sel = order[start:end]
        start = end
        rep = cnt[sel]
        tid = np.repeat(sel, rep)
        local = np.arange(rep.sum()) - np.repeat(np.cumsum(rep) - rep, rep)
        ii = i0[tid] + local // wj[tid]
        jj = j0[tid] + local % wj[tid]
        x = ii + 0.5
        y = jj + 0.5
        e0 = edge(a[tid], b[tid], x, y)
        e1 = edge(b[tid], c[tid], x, y)
        e2 = edge(c[tid], a[tid], x, y)
        inside = ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
        mask[ii[inside], jj[inside]] = True

    filled = int(mask.sum())
    # perimeter pixels: filled pixels with at least one empty 4-neighbour
    m = mask
    border = m & ~(np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1))
    area = filled * h * h
    unc = 0.5 * int(border.sum()) * h * h
    return ProjectedArea(area, unc, h, tuple(float(x) for x in np.asarray(direction) / np.linalg.norm(direction)), mask, (float(origin[0]), float(origin[1])))
