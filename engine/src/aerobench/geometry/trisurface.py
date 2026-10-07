"""Triangulated surface with named regions — the geometry workhorse."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np


@dataclass
class TriSurface:
    """Indexed triangle surface.

    * ``points``  (N, 3) float64
    * ``faces``   (M, 3) int64 vertex indices
    * ``regions`` (M,)  int32 region index per face
    * ``region_names`` region index -> name
    """

    points: np.ndarray
    faces: np.ndarray
    regions: np.ndarray = field(default=None)  # type: ignore[assignment]
    region_names: list[str] = field(default_factory=lambda: ["region0"])

    def __post_init__(self):
        self.points = np.ascontiguousarray(self.points, dtype=np.float64).reshape(-1, 3)
        self.faces = np.ascontiguousarray(self.faces, dtype=np.int64).reshape(-1, 3)
        if self.regions is None:
            self.regions = np.zeros(len(self.faces), dtype=np.int32)
        self.regions = np.ascontiguousarray(self.regions, dtype=np.int32).reshape(-1)
        if len(self.regions) != len(self.faces):
            raise ValueError("regions must have one entry per face")
        if len(self.faces) and (self.faces.min() < 0 or self.faces.max() >= len(self.points)):
            raise ValueError("face index out of range")
        if len(self.regions) and self.regions.max() >= len(self.region_names):
            raise ValueError("region index without a name")

    # ------------------------------------------------------------------ basic properties
    @property
    def n_faces(self) -> int:
        return int(len(self.faces))

    @property
    def n_points(self) -> int:
        return int(len(self.points))

    def copy(self) -> "TriSurface":
        return TriSurface(self.points.copy(), self.faces.copy(), self.regions.copy(), list(self.region_names))

    def bbox(self) -> tuple[np.ndarray, np.ndarray]:
        if self.n_faces == 0:
            z = np.zeros(3)
            return z, z
        used_mask = np.zeros(len(self.points), bool)
        used_mask[self.faces.reshape(-1)] = True
        used = self.points[used_mask]
        return used.min(axis=0), used.max(axis=0)

    def triangles(self) -> np.ndarray:
        return self.points[self.faces]  # (M, 3, 3)

    def face_normals_raw(self) -> np.ndarray:
        t = self.triangles()
        return np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])

    def face_areas(self) -> np.ndarray:
        return 0.5 * np.linalg.norm(self.face_normals_raw(), axis=1)

    def face_normals(self) -> np.ndarray:
        n = self.face_normals_raw()
        lens = np.linalg.norm(n, axis=1)
        out = np.zeros_like(n)
        ok = lens > 0
        out[ok] = n[ok] / lens[ok, None]
        return out

    def face_centres(self) -> np.ndarray:
        return self.triangles().mean(axis=1)

    def area(self) -> float:
        return float(self.face_areas().sum())

    def signed_volume(self, mask: Optional[np.ndarray] = None) -> float:
        t = self.triangles() if mask is None else self.triangles()[mask]
        return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)

    # ------------------------------------------------------------------ regions
    def region_index(self, name: str) -> int:
        return self.region_names.index(name)

    def subset_faces(self, mask: np.ndarray, compact: bool = True) -> "TriSurface":
        mask = np.asarray(mask, bool)
        faces = self.faces[mask]
        regions = self.regions[mask]
        if not compact:
            return TriSurface(self.points.copy(), faces, regions, list(self.region_names))
        used, inv = np.unique(faces.ravel(), return_inverse=True)
        pts = self.points[used]
        new_faces = inv.reshape(-1, 3)
        # compact region names
        used_regions = np.unique(regions)
        remap = -np.ones(len(self.region_names), dtype=np.int32)
        remap[used_regions] = np.arange(len(used_regions), dtype=np.int32)
        names = [self.region_names[i] for i in used_regions]
        return TriSurface(pts, new_faces, remap[regions] if len(regions) else regions, names or ["region0"])

    def subset_regions(self, names: Iterable[str]) -> "TriSurface":
        idx = [self.region_index(n) for n in names]
        return self.subset_faces(np.isin(self.regions, idx))

    def rename_region(self, old: str, new: str) -> None:
        self.region_names[self.region_index(old)] = new

    def region_face_counts(self) -> dict[str, int]:
        counts = np.bincount(self.regions, minlength=len(self.region_names))
        return {n: int(c) for n, c in zip(self.region_names, counts)}

    # ------------------------------------------------------------------ geometry ops
    def transformed(self, matrix4: np.ndarray) -> "TriSurface":
        m = np.asarray(matrix4, float)
        pts = self.points @ m[:3, :3].T + m[:3, 3]
        faces = self.faces
        # A transform with negative determinant flips orientation -> fix winding
        if np.linalg.det(m[:3, :3]) < 0:
            faces = faces[:, ::-1].copy()
        return TriSurface(pts, faces, self.regions.copy(), list(self.region_names))

    def translated(self, t: Sequence[float]) -> "TriSurface":
        s = self.copy()
        s.points = s.points + np.asarray(t, float)
        return s

    @staticmethod
    def merge(surfaces: Sequence["TriSurface"]) -> "TriSurface":
        pts, faces, regs, names = [], [], [], []
        off = 0
        for s in surfaces:
            pts.append(s.points)
            faces.append(s.faces + off)
            base = len(names)
            # merge identical region names
            remap = []
            for n in s.region_names:
                if n in names:
                    remap.append(names.index(n))
                else:
                    names.append(n)
                    remap.append(len(names) - 1)
            regs.append(np.asarray(remap, dtype=np.int32)[s.regions] if s.n_faces else s.regions)
            off += s.n_points
            _ = base
        if not pts:
            return TriSurface(np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros(0, np.int32), ["region0"])
        return TriSurface(np.vstack(pts), np.vstack(faces), np.concatenate(regs), names)

    # ------------------------------------------------------------------ topology helpers
    def edges(self) -> tuple[np.ndarray, np.ndarray]:
        """Unique undirected edges (E, 2) and, for each face-edge (3M), its unique edge index."""
        e = np.concatenate([self.faces[:, [0, 1]], self.faces[:, [1, 2]], self.faces[:, [2, 0]]])
        es = np.sort(e, axis=1)
        from .fastops import unique_int_rows

        uniq, inv = unique_int_rows(es, return_inverse=True)
        return uniq, inv.reshape(-1)

    def compact(self) -> "TriSurface":
        """Drop unreferenced points."""
        return self.subset_faces(np.ones(self.n_faces, bool))
