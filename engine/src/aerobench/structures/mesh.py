"""Structural surface mesh: the CFD surface of the analysed parts, as quadratic shell
triangles (CalculiX S6).

The same triangles the flow solution was computed on are used — posed exactly as in the CFD
case — so the wall loads map onto the structure without any interpolation across shapes.
Degenerate triangles are removed; closed parts are oriented with outward normals (the ply
order of a laminate runs from the outer surface inwards); very fine CAD tessellations are
coarsened to a size the direct solver handles, and coarse ones are divided (``refine``:
edge splits at existing surface points and diagonal swaps on flat patches, so the shape
does not change) until no element is longer than about the size half the element budget
allows. CAD exports are long thin triangles; the quadratic S6 shell is tolerant of that
shape (a cantilever of 125:1 slivers deflects within 10 % of beam theory) as long as the
elements are short enough along the direction the part bends, which the division ensures.

Two idealisations:
* ``skin``: the whole surface is a shell — hollow or foam-filled elements, moulded skins;
* ``plate``: only the upper (or lower) faces of a thin closed part form the shell, offset
  so the plate lies inside the part — splitters, endplates, flat panels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.geometry import ComponentRole
from ..domain.project import ProjectConfig
from ..domain.structure import StructureModel
from ..geometry.fastops import unique_int_rows
from ..geometry.pose import posed_components
from ..geometry.trisurface import TriSurface
from .refine import improve


@dataclass
class StructMesh:
    points: np.ndarray  # (N, 3) corner nodes
    tris: np.ndarray  # (M, 3) corner node indices, outward oriented where the part is closed
    part: np.ndarray  # (M,) index into part_ids
    part_ids: list[str]
    model: str = "skin"
    notes: list[str] = field(default_factory=list)
    mid_points: Optional[np.ndarray] = None  # (K, 3) mid-edge nodes
    tri_mids: Optional[np.ndarray] = None  # (M, 3) indices into mid_points for edges 01, 12, 20
    closed_parts: list[str] = field(default_factory=list)

    @property
    def n_tris(self) -> int:
        return int(len(self.tris))

    def normals(self) -> np.ndarray:
        t = self.points[self.tris]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        a = np.linalg.norm(n, axis=1)
        return n / np.where(a > 0, a, 1.0)[:, None]

    def areas(self) -> np.ndarray:
        t = self.points[self.tris]
        return 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)

    def centroids(self) -> np.ndarray:
        return self.points[self.tris].mean(axis=1)

    def build_quadratic(self) -> None:
        e = np.concatenate([self.tris[:, [0, 1]], self.tris[:, [1, 2]], self.tris[:, [2, 0]]])
        key = np.sort(e, axis=1)
        uniq, inv = unique_int_rows(key, return_inverse=True)
        self.mid_points = 0.5 * (self.points[uniq[:, 0]] + self.points[uniq[:, 1]])
        m = len(self.tris)
        self.tri_mids = np.stack([inv[:m], inv[m:2 * m], inv[2 * m:]], axis=1)

    def s6(self) -> tuple[np.ndarray, np.ndarray]:
        """All nodes (corners first) and S6 connectivity (0-based)."""
        if self.mid_points is None:
            self.build_quadratic()
        nodes = np.vstack([self.points, self.mid_points])
        conn = np.hstack([self.tris, self.tri_mids + len(self.points)])  # type: ignore[operator]
        return nodes, conn

    def quality(self) -> dict:
        t = self.points[self.tris]
        e = np.stack([np.linalg.norm(t[:, 1] - t[:, 0], axis=1), np.linalg.norm(t[:, 2] - t[:, 1], axis=1),
                      np.linalg.norm(t[:, 0] - t[:, 2], axis=1)], axis=1)
        area = self.areas()
        # normalised shape quality 4*sqrt(3)*A / sum(l^2): 1 = equilateral
        q = 4 * math.sqrt(3) * area / np.maximum((e ** 2).sum(axis=1), 1e-300)
        return {"triangles": self.n_tris, "nodes": int(len(self.points)), "min_quality": float(q.min()) if len(q) else None,
                "poor_triangles": int((q < 0.1).sum()), "edge_min": float(e.min()) if len(e) else None,
                "edge_max": float(e.max()) if len(e) else None, "edge_median": float(np.median(e)) if len(e) else None,
                "area": float(area.sum())}


def analysed_components(cfg: ProjectConfig) -> list[str]:
    ids = [c.id for c in cfg.geometry.components]
    if cfg.structure.components:
        bad = [c for c in cfg.structure.components if c not in ids]
        if bad:
            raise AeroBenchError.make("structure.unknown_part", ErrorCategory.CONFIGURATION,
                                      f"The structural check refers to unknown part(s): {', '.join(bad)}.",
                                      action="Choose the parts again on the Structure page.")
        return list(cfg.structure.components)
    return [c.id for c in cfg.geometry.components if c.role not in (ComponentRole.WHEEL, ComponentRole.STILT)]


def _clean(s: TriSurface) -> tuple[TriSurface, int]:
    t = s.points[s.faces]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    scale = float(np.median(area)) if len(area) else 0.0
    keep = (area > 1e-10 * max(scale, 1e-30)) & (s.faces[:, 0] != s.faces[:, 1]) & (s.faces[:, 1] != s.faces[:, 2]) & (s.faces[:, 0] != s.faces[:, 2])
    srt = np.sort(s.faces, axis=1)
    _, first = np.unique(srt, axis=0, return_index=True)
    dup = np.ones(len(s.faces), dtype=bool)
    dup[first] = False
    keep &= ~dup
    removed = int((~keep).sum())
    faces = s.faces[keep]
    used = np.unique(faces)
    remap = -np.ones(s.n_points, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return TriSurface(s.points[used], remap[faces], s.regions[keep], list(s.region_names)), removed


def _shells(faces: np.ndarray, n_points: int) -> np.ndarray:
    """Connected component label per face (faces sharing a vertex are connected)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(faces)
    rows = np.repeat(np.arange(m), 3)
    g = coo_matrix((np.ones(3 * m), (rows, faces.reshape(-1))), shape=(m, n_points)).tocsr()
    adj = g @ g.T
    _, lab = connected_components(adj, directed=False)
    return lab


def _closed(faces: np.ndarray) -> bool:
    e = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, cnt = unique_int_rows(e, return_counts=True)
    return bool(len(cnt)) and bool(np.all(cnt == 2))


def _orient_outward(points: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, list[bool]]:
    """Flip closed shells whose signed volume is negative. Returns faces and per-shell closedness."""
    lab = _shells(faces, len(points))
    out = faces.copy()
    closed = []
    for k in range(lab.max() + 1 if len(lab) else 0):
        sel = lab == k
        f = faces[sel]
        is_closed = _closed(f)
        closed.append(is_closed)
        if is_closed:
            t = points[f]
            vol = float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6.0
            if vol < 0:
                out[sel] = f[:, ::-1]
    return out, closed


def build_structural_mesh(project, cfg: ProjectConfig, decimate=None) -> StructMesh:
    """The analysed parts of ``cfg`` (a run's configuration snapshot), posed as in its case."""
    comps = analysed_components(cfg)
    if not comps:
        raise AeroBenchError.make("structure.no_parts", ErrorCategory.CONFIGURATION, "There is no part to analyse.",
                                  action="Choose at least one part on the Structure page.")
    cfd = project.cfd_surface(cfg=cfg)
    parts, _, _ = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
    surfs, notes = [], []
    for cid in comps:
        s, removed = _clean(parts[cid])
        if removed:
            notes.append(f"{removed} degenerate or duplicate triangle(s) of '{cid}' were left out.")
        surfs.append(TriSurface(s.points, s.faces, np.zeros(s.n_faces, np.int32), [cid]))
    merged = TriSurface.merge(surfs)
    st = cfg.structure
    limit = st.max_elements
    if merged.n_faces > limit:
        if decimate is None:
            raise AeroBenchError.make("structure.too_fine", ErrorCategory.CONFIGURATION,
                                      f"The parts have {merged.n_faces} triangles, more than the {limit} the structural model allows.",
                                      action="Raise the element limit on the Structure page, or simplify the parts.")
        merged, note = decimate(merged, limit)
        notes.append(note)
    faces, closed = _orient_outward(merged.points, merged.faces)
    part = merged.regions.astype(np.int64)
    closed_parts = []
    lab = _shells(faces, len(merged.points))
    for k, c in enumerate(closed):
        if c:
            closed_parts += sorted({merged.region_names[i] for i in np.unique(part[lab == k])})
    open_parts = sorted(set(comps) - set(closed_parts))
    if st.model == StructureModel.PLATE:
        n = np.cross(merged.points[faces[:, 1]] - merged.points[faces[:, 0]], merged.points[faces[:, 2]] - merged.points[faces[:, 0]])
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
        up = 1.0 if st.plate_side == "upper" else -1.0
        keep = n[:, 2] * up > math.cos(math.radians(60))
        if not keep.any():
            raise AeroBenchError.make("structure.plate_side", ErrorCategory.CONFIGURATION,
                                      f"No {st.plate_side} faces were found for the plate model.",
                                      action="Use the skin model, or the other side of the plate.")
        faces, part = faces[keep], part[keep]
        used = np.unique(faces)
        remap = -np.ones(len(merged.points), dtype=np.int64)
        remap[used] = np.arange(len(used))
        points = merged.points[used]
        faces = remap[faces]
        notes.append(f"Plate model: the {st.plate_side} faces of the parts carry the plate ({len(faces)} triangles).")
    else:
        points = merged.points
        if open_parts:
            notes.append("Not closed (open edges): " + ", ".join(open_parts) + ". Their shell normals follow the file; check the ply order.")
    points, faces, part = _refine(np.asarray(points, dtype=float), np.asarray(faces, dtype=np.int64), part, limit, notes)
    m = StructMesh(points=np.asarray(points, dtype=float), tris=np.asarray(faces, dtype=np.int64), part=part, part_ids=list(merged.region_names),
                   model=st.model.value, notes=notes, closed_parts=closed_parts)
    q = m.quality()
    if q["poor_triangles"] > 0.25 * q["triangles"]:
        m.notes.append(f"{q['poor_triangles']} of {q['triangles']} triangles are long and thin (CAD tessellation). The shell elements "
                       "tolerate this; stress peaks right next to them are less precise.")
    return m


def refine_target(area: float, limit: int) -> float:
    """Largest element edge (m) so that equilateral elements would use half the budget."""
    return math.sqrt(area / (0.433 * 0.5 * max(limit, 100)))


def _refine(points: np.ndarray, faces: np.ndarray, part: np.ndarray, limit: int, notes: list[str]):
    t = points[faces]
    area = float(0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum())
    if area <= 0 or len(faces) == 0:
        return points, faces, part
    target = refine_target(area, limit)
    P, F, R, info = improve(points, faces, part, target, max_faces=limit, tolerance=min(1e-5, 1e-3 * target))
    if info["faces_out"] > info["faces_in"]:
        size = f"edges were divided to at most about {info['size'] * 1250:.0f} mm" + (
            f" (as far as the limit of {limit} elements allows)" if info["budget_reached"] else "")
        notes.append(f"The surface was divided into {info['faces_out']} triangles (from {info['faces_in']}): {size}. The shape is unchanged.")
    return P, F, R
