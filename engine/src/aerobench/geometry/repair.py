"""Non-destructive geometry repair (BUILD_SPEC §4.3).

Every operation takes a surface and returns ``(new_surface, OperationRecord)``.  The
original is never modified; the record is stored in the geometry revision history.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

import numpy as np
from scipy.spatial import cKDTree

from .inspect import boundary_loops, edge_topology, face_components
from .fastops import unique_int_rows
from .trisurface import TriSurface


@dataclass
class OperationRecord:
    op: str
    params: dict[str, Any]
    summary: str
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def merge_vertices(surf: TriSurface, tolerance: Optional[float] = None) -> tuple[TriSurface, OperationRecord]:
    lo, hi = surf.bbox()
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    tol = tolerance if tolerance is not None else 1e-7 * diag
    tree = cKDTree(surf.points)
    pairs = tree.query_pairs(tol, output_type="ndarray")
    parent = np.arange(surf.n_points)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(surf.n_points)])
    faces = roots[surf.faces]
    out = TriSurface(surf.points, faces, surf.regions, list(surf.region_names)).compact()
    merged = surf.n_points - out.n_points
    return out, OperationRecord("merge_vertices", {"tolerance": tol}, f"Merged {merged} coincident vertices", {"merged": int(merged)})


def remove_degenerate(surf: TriSurface, rel_area: float = 1e-12) -> tuple[TriSurface, OperationRecord]:
    f = surf.faces
    rep = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
    tri = surf.triangles()
    emax = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max(axis=1)
    bad = rep | (surf.face_areas() <= rel_area * np.maximum(emax, 1e-30) ** 2)
    out = surf.subset_faces(~bad)
    return out, OperationRecord("remove_degenerate", {"rel_area": rel_area}, f"Removed {int(bad.sum())} degenerate triangles",
                                {"removed": int(bad.sum())})


def remove_duplicate_faces(surf: TriSurface) -> tuple[TriSurface, OperationRecord]:
    fs = np.sort(surf.faces, axis=1)
    _, first = unique_int_rows(fs, return_index=True)
    keep = np.zeros(surf.n_faces, bool)
    keep[first] = True
    out = surf.subset_faces(keep)
    n = surf.n_faces - out.n_faces
    return out, OperationRecord("remove_duplicate_faces", {}, f"Removed {n} duplicate triangles", {"removed": int(n)})


def orient_normals(surf: TriSurface, outward: bool = True) -> tuple[TriSurface, OperationRecord]:
    """Make orientation consistent within each connected component, then outward.

    Closed components use the signed volume; open components use the area-weighted
    direction of normals relative to the component centroid.
    """
    faces = surf.faces.copy()
    m = surf.n_faces
    uniq, inv, counts, _ = edge_topology(surf)
    # adjacency via manifold edges only
    face_of = np.tile(np.arange(m), 3)
    order = np.argsort(inv, kind="stable")
    inv_sorted = inv[order]
    starts = np.searchsorted(inv_sorted, np.arange(len(uniq)))
    ends = np.searchsorted(inv_sorted, np.arange(len(uniq)), side="right")
    nbrs: list[list[int]] = [[] for _ in range(m)]
    for e in np.nonzero(counts == 2)[0]:
        a, b = face_of[order[starts[e]]], face_of[order[ends[e] - 1]]
        nbrs[a].append(b)
        nbrs[b].append(a)

    def directed_edges(fi):
        f = faces[fi]
        return {(f[0], f[1]), (f[1], f[2]), (f[2], f[0])}

    visited = np.zeros(m, bool)
    flips = 0
    comp_id = -np.ones(m, int)
    nc = 0
    for seed in range(m):
        if visited[seed]:
            continue
        q = deque([seed])
        visited[seed] = True
        comp_id[seed] = nc
        while q:
            fi = q.popleft()
            de = directed_edges(fi)
            for nb in nbrs[fi]:
                if visited[nb]:
                    continue
                # neighbour must traverse the shared edge in the opposite direction
                if de & directed_edges(nb):
                    faces[nb] = faces[nb][::-1]
                    flips += 1
                visited[nb] = True
                comp_id[nb] = nc
                q.append(nb)
        nc += 1

    s2 = TriSurface(surf.points, faces, surf.regions, list(surf.region_names))
    comp_flips = 0
    if outward:
        n, labels = face_components(s2)
        open_face = np.zeros(m, bool)
        open_face[face_of[(counts == 1)[inv]]] = True
        centres = s2.face_centres()
        normals = s2.face_normals_raw()
        for c in range(n):
            mask = labels == c
            if not open_face[mask].any():
                wrong = s2.signed_volume(mask) < 0
            else:
                cen = centres[mask]
                wrong = float(np.einsum("ij,ij->", cen - cen.mean(axis=0), normals[mask])) < 0
            if wrong:
                faces[mask] = faces[mask][:, ::-1]
                comp_flips += 1
    out = TriSurface(surf.points, faces, surf.regions, list(surf.region_names))
    return out, OperationRecord("orient_normals", {"outward": outward},
                                f"Flipped {flips} triangles for consistency and {comp_flips} components to point outward",
                                {"flipped_faces": int(flips), "flipped_components": int(comp_flips)})


def fill_holes(surf: TriSurface, max_edges: int = 64, max_perimeter: Optional[float] = None) -> tuple[TriSurface, OperationRecord]:
    """Close small boundary loops with a centroid fan (adds one vertex per hole).

    Only loops that are closed and not larger than the limits are filled; new faces get
    the region of the adjacent boundary face.  Large openings are reported, not filled.
    """
    uniq, inv, counts, _ = edge_topology(surf)
    loops = boundary_loops(surf, uniq, counts, max_loops=100000)
    pts = [surf.points]
    new_faces, new_regs = [], []
    filled = skipped = 0
    # map boundary directed edge -> face region and direction
    f = surf.faces
    e_dir = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    face_of = np.tile(np.arange(surf.n_faces), 3)
    bd = {}
    for k in np.nonzero(counts[inv] == 1)[0]:
        a, b = int(e_dir[k, 0]), int(e_dir[k, 1])
        bd[(a, b)] = face_of[k]
    nxt = surf.n_points
    for lp in loops:
        verts = lp.get("vertices")
        if not lp["closed"] or verts is None or lp["n_edges"] > max_edges or (max_perimeter and lp["perimeter"] > max_perimeter):
            skipped += 1
            continue
        ring = verts[:-1] if verts[0] == verts[-1] else verts
        if len(ring) < 3:
            skipped += 1
            continue
        c = surf.points[ring].mean(axis=0)
        pts.append(c[None, :])
        ci = nxt
        nxt += 1
        region = 0
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            # boundary face has directed edge (a,b) or (b,a); the fill face must use the opposite
            if (a, b) in bd:
                new_faces.append([b, a, ci])
                region = surf.regions[bd[(a, b)]]
            else:
                new_faces.append([a, b, ci])
                if (b, a) in bd:
                    region = surf.regions[bd[(b, a)]]
            new_regs.append(region)
        filled += 1
    if not new_faces:
        return surf.copy(), OperationRecord("fill_holes", {"max_edges": max_edges, "max_perimeter": max_perimeter},
                                            f"No holes filled ({skipped} openings above the size limit)", {"filled": 0, "skipped": skipped})
    out = TriSurface(np.vstack(pts), np.vstack([surf.faces, np.array(new_faces)]),
                     np.concatenate([surf.regions, np.array(new_regs, np.int32)]), list(surf.region_names))
    return out, OperationRecord("fill_holes", {"max_edges": max_edges, "max_perimeter": max_perimeter},
                                f"Filled {filled} holes ({skipped} larger openings left open)", {"filled": filled, "skipped": skipped})


def remove_small_components(surf: TriSurface, min_area_fraction: float = 1e-5, min_faces: int = 0) -> tuple[TriSurface, OperationRecord]:
    n, labels = face_components(surf)
    areas = np.bincount(labels, weights=surf.face_areas(), minlength=n)
    nf = np.bincount(labels, minlength=n)
    total = areas.sum()
    small = (areas < min_area_fraction * total) | (nf < min_faces)
    keep = ~small[labels]
    out = surf.subset_faces(keep)
    return out, OperationRecord("remove_small_components", {"min_area_fraction": min_area_fraction, "min_faces": min_faces},
                                f"Removed {int(small.sum())} small components", {"removed": int(small.sum())})


def split_by_component(surf: TriSurface, prefix: Optional[str] = None) -> tuple[TriSurface, OperationRecord]:
    """Assign each connected component its own region (largest first)."""
    n, labels = face_components(surf)
    areas = np.bincount(labels, weights=surf.face_areas(), minlength=n)
    order = np.argsort(-areas)
    rank = np.empty(n, int)
    rank[order] = np.arange(n)
    names = [f"{prefix or 'part'}{i}" for i in range(n)]
    out = TriSurface(surf.points, surf.faces, rank[labels].astype(np.int32), names)
    return out, OperationRecord("split_by_component", {"prefix": prefix}, f"Split into {n} regions by connectivity", {"regions": n})


def split_by_angle(surf: TriSurface, feature_angle_deg: float = 60.0, min_faces: int = 1) -> tuple[TriSurface, OperationRecord]:
    """Region-grow across edges whose dihedral angle is below the feature angle."""
    uniq, inv, counts, _ = edge_topology(surf)
    m = surf.n_faces
    face_of = np.tile(np.arange(m), 3)
    order = np.argsort(inv, kind="stable")
    inv_sorted = inv[order]
    starts = np.searchsorted(inv_sorted, np.arange(len(uniq)))
    nrm = surf.face_normals()
    cos_lim = np.cos(np.radians(feature_angle_deg))
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    e2 = np.nonzero(counts == 2)[0]
    a = face_of[order[starts[e2]]]
    b = face_of[order[starts[e2] + 1]]
    smooth = np.einsum("ij,ij->i", nrm[a], nrm[b]) >= cos_lim
    a, b = a[smooth], b[smooth]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(m, m))
    n, labels = connected_components(g, directed=False)
    names = [f"patch{i}" for i in range(n)]
    out = TriSurface(surf.points, surf.faces, labels.astype(np.int32), names)
    return out, OperationRecord("split_by_angle", {"feature_angle_deg": feature_angle_deg}, f"Split into {n} regions by feature angle",
                                {"regions": int(n)})


def simplify_vertex_clustering(surf: TriSurface, cell_size: float) -> tuple[TriSurface, OperationRecord]:
    """Fast, robust decimation by vertex clustering on a uniform grid (preserves regions)."""
    if cell_size <= 0:
        raise ValueError("cell_size must be positive")
    lo, _ = surf.bbox()
    key = np.floor((surf.points - lo) / cell_size).astype(np.int64)
    cluster = unique_int_rows(key, return_inverse=True)[1].reshape(-1)
    ncl = int(cluster.max()) + 1
    sums = np.zeros((ncl, 3))
    np.add.at(sums, cluster, surf.points)
    cnt = np.bincount(cluster, minlength=ncl)[:, None]
    pts = sums / cnt
    faces = cluster[surf.faces]
    ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    out = TriSurface(pts, faces[ok], surf.regions[ok], list(surf.region_names)).compact()
    out, _ = remove_duplicate_faces(out)
    return out, OperationRecord("simplify", {"method": "vertex_clustering", "cell_size": cell_size},
                                f"Simplified {surf.n_faces} → {out.n_faces} triangles", {"before": surf.n_faces, "after": out.n_faces})


REPAIR_OPERATIONS: dict[str, Callable[..., tuple[TriSurface, OperationRecord]]] = {
    "merge_vertices": merge_vertices,
    "remove_degenerate": remove_degenerate,
    "remove_duplicate_faces": remove_duplicate_faces,
    "orient_normals": orient_normals,
    "fill_holes": fill_holes,
    "remove_small_components": remove_small_components,
    "split_by_component": split_by_component,
    "split_by_angle": split_by_angle,
    "simplify": simplify_vertex_clustering,
}

DEFAULT_PIPELINE = [
    ("merge_vertices", {}),
    ("remove_degenerate", {}),
    ("remove_duplicate_faces", {}),
    ("remove_small_components", {"min_area_fraction": 1e-5}),
    ("orient_normals", {"outward": True}),
    ("fill_holes", {"max_edges": 32}),
]


def run_pipeline(surf: TriSurface, steps: list[tuple[str, dict]] | None = None) -> tuple[TriSurface, list[OperationRecord]]:
    records = []
    cur = surf
    for name, params in steps or DEFAULT_PIPELINE:
        fn = REPAIR_OPERATIONS[name]
        cur, rec = fn(cur, **params)
        records.append(rec)
    return cur, records
