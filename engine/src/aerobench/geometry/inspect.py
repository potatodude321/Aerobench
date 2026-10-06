"""Geometry diagnostics (BUILD_SPEC §4.2)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..core.errors import Diagnostic, ErrorCategory, Severity
from .trisurface import TriSurface

UNIT_CANDIDATES = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "in": 0.0254, "ft": 0.3048}


@dataclass
class ComponentInfo:
    index: int
    n_faces: int
    area: float
    bbox_min: list[float]
    bbox_max: list[float]
    closed: bool
    signed_volume: float
    regions: list[str]


@dataclass
class UnitSuggestion:
    unit: str
    scale: float
    resulting_length_m: float
    plausible: bool


@dataclass
class GeometryReport:
    n_points: int
    n_faces: int
    bbox_min: list[float]
    bbox_max: list[float]
    extents: list[float]
    surface_area: float
    region_names: list[str]
    region_face_counts: dict[str, int]
    degenerate_faces: int
    duplicate_faces: int
    boundary_edges: int
    non_manifold_edges: int
    inconsistent_orientation_edges: int
    boundary_loops: list[dict]
    components: list[ComponentInfo]
    closed: bool
    outward_normals: Optional[bool]
    units_assumed: str
    unit_suggestions: list[UnitSuggestion]
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["diagnostics"] = [x.model_dump(mode="json") for x in self.diagnostics]
        return d


def edge_topology(surf: TriSurface):
    """Return (unique_edges, edge_face_count, directed info) for manifold/orientation checks."""
    f = surf.faces
    e_dir = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])  # directed, 3M
    e_sorted = np.sort(e_dir, axis=1)
    uniq, inv, counts = np.unique(e_sorted, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    # orientation: for an edge shared by exactly two faces, directions must be opposite
    forward = (e_dir[:, 0] < e_dir[:, 1]).astype(np.int64)  # 1 if stored as (lo, hi)
    fwd_count = np.bincount(inv, weights=forward, minlength=len(uniq))
    two = counts == 2
    inconsistent = int(np.sum(two & (fwd_count != 1)))
    return uniq, inv, counts, inconsistent


def face_components(surf: TriSurface, inv: Optional[np.ndarray] = None) -> tuple[int, np.ndarray]:
    """Connected components of faces through shared edges."""
    m = surf.n_faces
    if m == 0:
        return 0, np.zeros(0, int)
    if inv is None:
        _, inv, _, _ = edge_topology(surf)
    face_of = np.tile(np.arange(m), 3)
    # connect faces sharing an edge: build edge->face incidence and project
    n_e = int(inv.max()) + 1
    inc = coo_matrix((np.ones(3 * m), (face_of, inv)), shape=(m, n_e)).tocsr()
    adj = inc @ inc.T
    n, labels = connected_components(adj, directed=False)
    return n, labels


def boundary_loops(surf: TriSurface, uniq: np.ndarray, counts: np.ndarray, max_loops: int = 200) -> list[dict]:
    """Chain boundary edges into loops; returns loop sizes/perimeters (largest first)."""
    b = uniq[counts == 1]
    if len(b) == 0:
        return []
    from collections import defaultdict

    adj: dict[int, list[int]] = defaultdict(list)
    for a, c in b:
        adj[int(a)].append(int(c))
        adj[int(c)].append(int(a))
    seen_edges: set[tuple[int, int]] = set()
    loops = []
    for a0, c0 in b:
        a0, c0 = int(a0), int(c0)
        key = (min(a0, c0), max(a0, c0))
        if key in seen_edges:
            continue
        loop = [a0]
        prev, cur = a0, c0
        seen_edges.add(key)
        closed = False
        for _ in range(len(b) + 1):
            loop.append(cur)
            nxt = [v for v in adj[cur] if (min(cur, v), max(cur, v)) not in seen_edges]
            if not nxt:
                closed = cur == a0 or a0 in adj[cur]
                break
            prev, cur = cur, nxt[0]
            seen_edges.add((min(prev, cur), max(prev, cur)))
            if cur == a0:
                closed = True
                break
        pts = surf.points[loop]
        perim = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
        loops.append({"n_edges": len(loop) - 1, "perimeter": perim, "closed": closed, "vertices": loop if len(loop) <= 2000 else None})
        if len(loops) >= max_loops:
            break
    loops.sort(key=lambda d: -d["perimeter"])
    return loops


def suggest_units(length_raw: float) -> list[UnitSuggestion]:
    out = []
    for unit, s in UNIT_CANDIDATES.items():
        L = length_raw * s
        out.append(UnitSuggestion(unit, s, L, 0.2 <= L <= 25.0))
    # rank: plausible first, then closeness to a 4.5 m road car / 1 m scale model
    def score(u: UnitSuggestion) -> float:
        if u.resulting_length_m <= 0:
            return 1e9
        d = min(abs(math.log(u.resulting_length_m / 4.5)), abs(math.log(u.resulting_length_m / 1.0)) + 0.3)
        pref = {"m": 0.0, "mm": 0.05, "cm": 0.4, "in": 0.6, "ft": 0.8}[u.unit]
        return (0 if u.plausible else 100) + d + pref

    return sorted(out, key=score)


def inspect_surface(surf: TriSurface, units_assumed: str = "m", length_scale: Optional[float] = None) -> GeometryReport:
    lo, hi = surf.bbox()
    ext = hi - lo
    L = float(ext.max()) if surf.n_faces else 0.0
    areas = surf.face_areas()
    diag: list[Diagnostic] = []

    # degenerate: tiny area relative to max edge^2 (slivers) or repeated indices
    tri = surf.triangles()
    e = np.stack([tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 1], tri[:, 0] - tri[:, 2]], axis=1)
    emax = np.linalg.norm(e, axis=2).max(axis=1)
    rep = (surf.faces[:, 0] == surf.faces[:, 1]) | (surf.faces[:, 1] == surf.faces[:, 2]) | (surf.faces[:, 0] == surf.faces[:, 2])
    degenerate = rep | (areas <= 1e-12 * np.maximum(emax, 1e-30) ** 2) | (areas == 0)
    n_degen = int(degenerate.sum())

    fs = np.sort(surf.faces, axis=1)
    _, first_idx, dup_counts = np.unique(fs, axis=0, return_index=True, return_counts=True)
    n_dup = int(surf.n_faces - len(first_idx))

    uniq, inv, counts, inconsistent = edge_topology(surf)
    n_boundary = int(np.sum(counts == 1))
    n_nonmanifold = int(np.sum(counts > 2))
    loops = boundary_loops(surf, uniq, counts)

    ncomp, labels = face_components(surf, inv)
    comps: list[ComponentInfo] = []
    edge_open = counts == 1
    # per-component closedness: any boundary edge whose faces belong to it
    face_of = np.tile(np.arange(surf.n_faces), 3)
    open_faces = np.zeros(surf.n_faces, bool)
    open_faces[face_of[edge_open[inv]]] = True
    for c in range(ncomp):
        mask = labels == c
        sub = surf.points[np.unique(surf.faces[mask])]
        regs = sorted({surf.region_names[r] for r in np.unique(surf.regions[mask])})
        comps.append(
            ComponentInfo(
                index=c,
                n_faces=int(mask.sum()),
                area=float(areas[mask].sum()),
                bbox_min=sub.min(axis=0).tolist(),
                bbox_max=sub.max(axis=0).tolist(),
                closed=not bool(open_faces[mask].any()),
                signed_volume=surf.signed_volume(mask),
                regions=regs,
            )
        )
    comps.sort(key=lambda c: -c.area)
    closed = n_boundary == 0 and n_nonmanifold == 0
    outward: Optional[bool] = None
    closed_comps = [c for c in comps if c.closed]
    if closed_comps:
        outward = all(c.signed_volume > 0 for c in closed_comps)

    units = suggest_units(L)
    report = GeometryReport(
        n_points=surf.n_points,
        n_faces=surf.n_faces,
        bbox_min=lo.tolist(),
        bbox_max=hi.tolist(),
        extents=ext.tolist(),
        surface_area=float(areas.sum()),
        region_names=list(surf.region_names),
        region_face_counts=surf.region_face_counts(),
        degenerate_faces=n_degen,
        duplicate_faces=n_dup,
        boundary_edges=n_boundary,
        non_manifold_edges=n_nonmanifold,
        inconsistent_orientation_edges=inconsistent,
        boundary_loops=[{k: v for k, v in lp.items() if k != "vertices"} for lp in loops[:50]],
        components=comps,
        closed=closed,
        outward_normals=outward,
        units_assumed=units_assumed,
        unit_suggestions=units,
    )

    # ---------------------------------------------------------------- diagnostics
    def add(code, sev, summary, cause="", action="", **ctx):
        diag.append(Diagnostic(code=code, category=ErrorCategory.GEOMETRY, severity=sev, summary=summary,
                               likely_cause=cause, action=action, context=ctx))

    if surf.n_faces == 0:
        add("geometry.empty", Severity.ERROR, "The geometry contains no triangles.")
        report.diagnostics = diag
        return report
    scale = UNIT_CANDIDATES.get(units_assumed, 1.0)
    L_m = L * scale
    if not (0.2 <= L_m <= 25.0):
        best = units[0]
        add(
            "geometry.units_suspicious",
            Severity.WARNING,
            f"The geometry is {L_m:.4g} m long when read as {units_assumed}, which is implausible for a vehicle.",
            "The file was exported in different units.",
            f"Interpret the file as '{best.unit}' (vehicle length {best.resulting_length_m:.3g} m).",
            suggested_unit=best.unit,
            suggested_scale=best.scale,
        )
    if n_degen:
        add("geometry.degenerate", Severity.WARNING, f"{n_degen} degenerate (zero-area or sliver) triangles.",
            "Tessellation artefacts from the exporting tool.", "Run Repair → Remove degenerate triangles.", count=n_degen)
    if n_dup:
        add("geometry.duplicate_faces", Severity.WARNING, f"{n_dup} duplicate triangles.", "Overlapping exports or merged parts.",
            "Run Repair → Remove duplicate faces.", count=n_dup)
    if n_nonmanifold:
        add("geometry.non_manifold", Severity.WARNING, f"{n_nonmanifold} non-manifold edges (shared by more than two triangles).",
            "Touching or intersecting parts exported as one surface, or internal faces.",
            "snappyHexMesh usually tolerates this, but check refinement near these edges; split parts if possible.",
            count=n_nonmanifold)
    if n_boundary:
        big = loops[0]["perimeter"] if loops else 0.0
        sev = Severity.WARNING
        add("geometry.open", sev,
            f"The surface is not closed: {len(loops)} open boundary loop(s), {n_boundary} boundary edges (largest gap perimeter {big:.3g}).",
            "Holes from missing CAD faces or deliberately open parts (e.g. cooling ducts).",
            "Small holes can be filled automatically; large openings need a leak-tight surface or a refinement "
            "level fine enough that snappyHexMesh does not leak through them.", loops=len(loops), largest_perimeter=big)
    if inconsistent:
        add("geometry.orientation", Severity.WARNING, f"{inconsistent} edges have inconsistent triangle orientation.",
            "Mixed normal directions.", "Run Repair → Orient normals.", count=inconsistent)
    if outward is False:
        add("geometry.inward_normals", Severity.WARNING, "Some closed parts have inward-pointing normals.",
            "Reversed export.", "Run Repair → Orient normals (outward).")
    small = [c for c in comps if c.area < 1e-5 * report.surface_area]
    if small:
        add("geometry.tiny_islands", Severity.INFO, f"{len(small)} tiny disconnected islands.",
            "Debris from CAD export.", "Run Repair → Remove small components.", count=len(small))
    report.diagnostics = diag
    return report
