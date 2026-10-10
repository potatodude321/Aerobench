"""Geometry parameterisation: rigid moves, free-form deformation (FFD) and radial-basis
morphing applied to selected components of a CFD-frame surface (ADR-0009).

Deformations act on the *unposed* CFD-frame surface (metres, nose at -x, +z up).  The
displacement of each parameter is linear in its value relative to the baseline value, so a
parameter at its baseline leaves the geometry bit-identical.

Topology is never changed (vertices move, faces do not), so a deformed surface keeps its
regions, closedness and component assignment.  :func:`check_deformation` decides whether
the moved surface is still a valid CFD geometry (no new self-intersections, no folded or
collapsed triangles, displacement limits, frozen components untouched, no ground contact).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

import numpy as np

from ..domain.exploration import FFDDef, GeometryParameter, RBFDef, ZoneDef
from ..domain.geometry import ComponentRole, GeometryComponent
from ..domain.vehicle import Wheel
from .fastops import unique_int_rows
from .trisurface import TriSurface

RIGID_KINDS = ("translate", "rotate")


# --------------------------------------------------------------------------- helpers


def _vec(v: Sequence[float]) -> np.ndarray:
    return np.asarray(v, dtype=np.float64).reshape(3)


def rotation_matrix(axis: Sequence[float], angle_rad: float) -> np.ndarray:
    a = _vec(axis)
    n = np.linalg.norm(a)
    if n == 0:
        raise ValueError("rotation axis must be non-zero")
    a = a / n
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(angle_rad) * K + (1 - math.cos(angle_rad)) * (K @ K)


def _bernstein(n: int, t: np.ndarray) -> np.ndarray:
    """(len(t), n+1) Bernstein basis of degree n."""
    t = np.clip(t, 0.0, 1.0)[:, None]
    k = np.arange(n + 1)[None, :]
    comb = np.array([math.comb(n, i) for i in range(n + 1)], dtype=np.float64)[None, :]
    return comb * t**k * (1 - t) ** (n - k)


def wendland_c2(r: np.ndarray) -> np.ndarray:
    r = np.asarray(r)
    return np.where(r < 1.0, (1.0 - r) ** 4 * (4.0 * r + 1.0), 0.0)


def _tps(r: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        v = r * r * np.log(r)
    return np.where(r > 0, v, 0.0)


# --------------------------------------------------------------------------- per-kind displacement fields


def ffd_displacement(pts: np.ndarray, d: FFDDef, value: float) -> np.ndarray:
    lo, hi = _vec(d.box_min), _vec(d.box_max)
    span = hi - lo
    if np.any(span <= 0):
        raise ValueError("FFD box must have positive size in every direction")
    l, m, n = (int(x) for x in d.divisions)
    if min(l, m, n) < 1:
        raise ValueError("FFD divisions must be >= 1")
    dP = np.zeros((l + 1, m + 1, n + 1, 3))
    for (i, j, k), direction in d.moves:
        if not (0 <= i <= l and 0 <= j <= m and 0 <= k <= n):
            raise ValueError(f"FFD control point ({i},{j},{k}) outside the {l + 1}x{m + 1}x{n + 1} lattice")
        if d.frozen_boundary and (i in (0, l) or j in (0, m) or k in (0, n)):
            raise ValueError(f"FFD control point ({i},{j},{k}) lies on the lattice boundary, which is frozen for continuity")
        dP[i, j, k] += _vec(direction)
    out = np.zeros_like(pts)
    inside = np.all((pts >= lo) & (pts <= hi), axis=1)
    if not inside.any() or not np.any(dP):
        return out
    stu = (pts[inside] - lo) / span
    Bs, Bt, Bu = _bernstein(l, stu[:, 0]), _bernstein(m, stu[:, 1]), _bernstein(n, stu[:, 2])
    out[inside] = value * np.einsum("pi,pj,pk,ijkc->pc", Bs, Bt, Bu, dP)
    return out


def smootherstep(t: np.ndarray) -> np.ndarray:
    """C2 ramp 0 -> 1 on [0, 1]; S(t) + S(1 - t) = 1."""
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def zone_window(pts: np.ndarray, lo: np.ndarray, hi: np.ndarray, blend: float) -> np.ndarray:
    """1 inside the zone box, fading C2-smoothly to 0 over ``blend`` inside each face, 0 outside."""
    b = np.minimum(blend, 0.5 * (hi - lo))
    d = np.minimum(pts - lo, hi - pts)  # distance to the nearest face per axis (< 0 outside)
    w = np.ones(len(pts))
    for a in range(3):
        w *= smootherstep(d[:, a] / b[a])
    return w


def zone_section(x: np.ndarray, lo: float, hi: float, blend: float, segments: int, k: int) -> np.ndarray:
    """Smooth partition of unity along x: section k of ``segments`` peaks at 1 at its node.
    The nodes span the full-strength part of the zone (inside the blend bands)."""
    if segments == 1:
        return np.ones(len(x))
    b = min(blend, 0.5 * (hi - lo))
    a0, a1 = lo + b, hi - b
    u = np.clip((x - a0) / max(a1 - a0, 1e-12), 0.0, 1.0)
    node = k / (segments - 1)
    return smootherstep(1.0 - np.abs(u - node) * (segments - 1))


def zone_displacement(pts: np.ndarray, d: ZoneDef, value: float, centreline_y: float = 0.0) -> np.ndarray:
    lo, hi = _vec(d.box_min), _vec(d.box_max)
    out = np.zeros_like(pts)
    inside = np.all((pts > lo) & (pts < hi), axis=1)
    if not inside.any() or value == 0.0:
        return out
    p = pts[inside]
    amp = value * d.metres_per_unit * zone_window(p, lo, hi, d.blend) * zone_section(p[:, 0], lo[0], hi[0], d.blend, d.segments, d.segment)
    if d.direction == "x":
        out[inside, 0] = amp
    elif d.direction == "z":
        out[inside, 2] = amp
    else:  # width: scaling about the centreline, continuous across it
        out[inside, 1] = amp * np.clip((p[:, 1] - centreline_y) / d.half_width, -1.0, 1.0)
    return out


@dataclass
class RBFSolution:
    centres: np.ndarray
    weights: np.ndarray  # (n, 3)
    poly: Optional[np.ndarray]  # (4, 3) for thin plate
    radius: float
    basis: str

    def __call__(self, pts: np.ndarray, chunk: int = 200_000) -> np.ndarray:
        out = np.zeros_like(pts)
        for s in range(0, len(pts), chunk):
            p = pts[s : s + chunk]
            r = np.linalg.norm(p[:, None, :] - self.centres[None, :, :], axis=2) / self.radius
            phi = wendland_c2(r) if self.basis == "wendland_c2" else _tps(r)
            v = phi @ self.weights
            if self.poly is not None:
                v += np.hstack([np.ones((len(p), 1)), p]) @ self.poly
            out[s : s + chunk] = v
        return out


def rbf_solve(d: RBFDef, value: float) -> RBFSolution:
    handles = [(_vec(p), _vec(u) * value) for p, u in d.handles]
    centres = np.array([h[0] for h in handles] + [_vec(p) for p in d.fixed_points]).reshape(-1, 3)
    disp = np.array([h[1] for h in handles] + [np.zeros(3)] * len(d.fixed_points)).reshape(-1, 3)
    if len(centres) == 0:
        raise ValueError("RBF deformation needs at least one handle")
    dists = np.linalg.norm(centres[:, None] - centres[None, :], axis=2)
    np.fill_diagonal(dists, np.inf)
    if len(centres) > 1 and dists.min() < 1e-9:
        raise ValueError("RBF handles/fixed points must be distinct")
    np.fill_diagonal(dists, 0.0)
    r = dists / d.support_radius
    if d.basis == "wendland_c2":
        A = wendland_c2(r)
        w = np.linalg.solve(A, disp)  # Wendland C2 is strictly positive definite in R^3
        return RBFSolution(centres, w, None, d.support_radius, d.basis)
    n = len(centres)
    P = np.hstack([np.ones((n, 1)), centres])
    A = np.zeros((n + 4, n + 4))
    A[:n, :n] = _tps(r)
    A[:n, n:] = P
    A[n:, :n] = P.T
    rhs = np.vstack([disp, np.zeros((4, 3))])
    sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    return RBFSolution(centres, sol[:n], sol[n:], d.support_radius, d.basis)


# --------------------------------------------------------------------------- application


def displacement_field(p: GeometryParameter, dv: float, centreline_y: float = 0.0):
    """Callable ``points (n,3) -> displacement (n,3)`` for parameter ``p`` at value offset ``dv``.

    With ``symmetric_about_centreline`` the field is symmetrised analytically,
    ``d_s(x) = ½ (d(x) + M d(Mx))`` with ``M = diag(1, -1, 1)``, so the result is exactly
    mirror-symmetric whatever the surface triangulation."""
    kind = p.deformation.kind
    if kind == "translate":
        t = _vec(p.deformation.direction) * dv  # type: ignore[union-attr]

        def f(x: np.ndarray) -> np.ndarray:
            return np.broadcast_to(t, x.shape).copy()
    elif kind == "rotate":
        R = rotation_matrix(p.deformation.axis, math.radians(dv))  # type: ignore[union-attr]
        piv = _vec(p.deformation.pivot)  # type: ignore[union-attr]

        def f(x: np.ndarray) -> np.ndarray:
            return (x - piv) @ R.T + piv - x
    elif kind == "ffd":
        d = p.deformation

        def f(x: np.ndarray) -> np.ndarray:
            return ffd_displacement(x, d, dv)  # type: ignore[arg-type]
    elif kind == "rbf":
        f = rbf_solve(p.deformation, dv)  # type: ignore[arg-type,assignment]
    elif kind == "zone":
        zd = p.deformation

        def f(x: np.ndarray) -> np.ndarray:
            return zone_displacement(x, zd, dv, centreline_y)  # type: ignore[arg-type]
    else:  # pragma: no cover - guarded by the discriminated union
        raise ValueError(f"unknown deformation kind {kind}")
    if not p.symmetric_about_centreline:
        return f
    M = np.array([1.0, -1.0, 1.0])
    shift = np.array([0.0, 2.0 * centreline_y, 0.0])

    def fs(x: np.ndarray) -> np.ndarray:  # mirror about the plane y = centreline_y
        return 0.5 * (f(x) + f(x * M + shift) * M)

    return fs


def component_vertex_masks(surf: TriSurface, components: list[GeometryComponent]) -> dict[str, np.ndarray]:
    """Boolean vertex mask per component (vertices referenced by the component's faces)."""
    name_to_idx = {n: i for i, n in enumerate(surf.region_names)}
    out: dict[str, np.ndarray] = {}
    for c in components:
        ridx = [name_to_idx[r] for r in c.source_regions if r in name_to_idx]
        fmask = np.isin(surf.regions, ridx)
        vm = np.zeros(surf.n_points, dtype=bool)
        vm[np.unique(surf.faces[fmask])] = True
        out[c.id] = vm
    return out


def affected_component_ids(param: GeometryParameter, components: list[GeometryComponent]) -> list[str]:
    if param.components:
        return [c for c in param.components if c not in param.frozen_components]
    return [c.id for c in components if c.role not in (ComponentRole.WHEEL, ComponentRole.STILT) and c.id not in param.frozen_components]


@dataclass
class DeformResult:
    surface: TriSurface
    displacement: np.ndarray  # (N, 3) total displacement per vertex
    wheels: list[Wheel]
    per_parameter_max: dict[str, float] = field(default_factory=dict)
    pinned_vertices: int = 0  # vertices shared with frozen components that would have moved
    notes: list[str] = field(default_factory=list)


def apply_parameters(
    surf: TriSurface,
    components: list[GeometryComponent],
    wheels: list[Wheel],
    parameters: list[GeometryParameter],
    values: dict[str, float],
    centreline_y: float = 0.0,
) -> DeformResult:
    """Apply all parameters (in definition order) and return the deformed surface.

    Each parameter displaces only the vertices of its affected components; vertices shared
    with an unaffected component stay fixed (the unaffected component wins).  Wheels whose
    component is moved rigidly have their centre/axis moved with it; wheels cannot be
    deformed non-rigidly (their rotation model needs an exact axis and radius).
    """
    comps = {c.id: c for c in components}
    masks = component_vertex_masks(surf, components)
    pts = surf.points.copy()
    total = np.zeros_like(pts)
    new_wheels = [w.model_copy() for w in wheels]
    wheel_by_comp = {w.component_id: i for i, w in enumerate(new_wheels) if w.component_id}
    res = DeformResult(surf, total, new_wheels)
    for p in parameters:
        if p.id not in values:
            continue
        dv = float(values[p.id]) - p.baseline
        if not (p.lower - 1e-12 <= float(values[p.id]) <= p.upper + 1e-12):
            raise ValueError(f"parameter {p.id}={values[p.id]} outside [{p.lower}, {p.upper}]")
        if dv == 0.0:
            res.per_parameter_max[p.id] = 0.0
            continue
        aff = affected_component_ids(p, components)
        unknown = [c for c in aff if c not in comps]
        if unknown:
            raise ValueError(f"parameter {p.id}: unknown component(s) {', '.join(unknown)}")
        kind = p.deformation.kind
        wheel_targets = [c for c in aff if comps[c].role == ComponentRole.WHEEL]
        if wheel_targets and kind not in RIGID_KINDS:
            raise ValueError(f"parameter {p.id}: wheels can only be moved rigidly (translate/rotate), not by {kind}")
        if wheel_targets and p.symmetric_about_centreline:
            raise ValueError(f"parameter {p.id}: centreline symmetrisation cannot be combined with moving wheels")
        move = np.zeros(surf.n_points, dtype=bool)
        for c in aff:
            move |= masks[c]
        keep = np.zeros(surf.n_points, dtype=bool)
        for cid, m in masks.items():
            if cid not in aff:
                keep |= m
        field_fn = displacement_field(p, dv, centreline_y)
        d = np.zeros_like(pts)
        d[move] = field_fn(pts[move])
        shared = move & keep
        if shared.any():
            res.pinned_vertices += int(np.count_nonzero(np.linalg.norm(d[shared], axis=1) > 1e-12))
            d[shared] = 0.0
        pts = pts + d
        total += d
        mx = float(np.linalg.norm(d, axis=1).max()) if len(d) else 0.0
        res.per_parameter_max[p.id] = mx
        for c in wheel_targets:
            wi = wheel_by_comp.get(c)
            if wi is None:
                continue
            w = new_wheels[wi]
            if kind == "translate":
                new_wheels[wi] = w.model_copy(update={"centre": tuple((_vec(w.centre) + _vec(p.deformation.direction) * dv).tolist())})  # type: ignore[union-attr]
            else:
                R = rotation_matrix(p.deformation.axis, math.radians(dv))  # type: ignore[union-attr]
                piv = _vec(p.deformation.pivot)  # type: ignore[union-attr]
                c_new = R @ (_vec(w.centre) - piv) + piv
                a_new = R @ _vec(w.axis)
                new_wheels[wi] = Wheel.model_validate({**w.model_dump(), "centre": tuple(c_new.tolist()), "axis": tuple(a_new.tolist())})
    res.surface = TriSurface(pts, surf.faces.copy(), surf.regions.copy(), list(surf.region_names))
    res.displacement = total
    res.wheels = new_wheels
    return res


# --------------------------------------------------------------------------- validity


@dataclass
class DeformCheck:
    valid: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


def _tri_quality(tris: np.ndarray) -> np.ndarray:
    """Normalised shape quality 4*sqrt(3)*A / sum(l^2) in [0, 1] (1 = equilateral)."""
    a = tris[:, 1] - tris[:, 0]
    b = tris[:, 2] - tris[:, 1]
    c = tris[:, 0] - tris[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(a, -c), axis=1)
    s = (a * a).sum(1) + (b * b).sum(1) + (c * c).sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = 4.0 * math.sqrt(3.0) * area / s
    return np.nan_to_num(q)


def edge_triangle_hits(points: np.ndarray, faces: np.ndarray, edge_faces: np.ndarray, chunk: int = 40_000) -> set[tuple[int, int]]:
    """Pairs (face_a, face_b), a < b, where an edge of a face in ``edge_faces`` pierces another
    face (faces sharing a vertex are excluded).

    Memory is bounded by processing the edge faces in spatially sorted chunks; each chunk
    uses a uniform-grid broad phase (vectorised) and a Möller–Trumbore segment/triangle test."""
    edge_faces = np.asarray(edge_faces, dtype=np.int64)
    if len(edge_faces) == 0:
        return set()
    tris = points[faces]
    lo_f = tris.min(axis=1)
    hi_f = tris.max(axis=1)
    cen = 0.5 * (lo_f[edge_faces] + hi_f[edge_faces])
    axis = int(np.argmax(cen.max(axis=0) - cen.min(axis=0)))
    ordered = edge_faces[np.argsort(cen[:, axis], kind="stable")]
    ext = (hi_f[edge_faces] - lo_f[edge_faces]).max(axis=1)
    cell = float(max(np.median(ext) * 1.5, 1e-9))
    hits: set[tuple[int, int]] = set()
    for s0 in range(0, len(ordered), chunk):
        hits |= _hits_core(tris, faces, lo_f, hi_f, ordered[s0 : s0 + chunk], cell)
    return hits


def _hits_core(tris: np.ndarray, faces: np.ndarray, lo_f: np.ndarray, hi_f: np.ndarray, edge_faces: np.ndarray, cell: float,
               max_pairs: int = 6_000_000) -> set[tuple[int, int]]:
    # only faces whose bounding box overlaps the region spanned by the edge faces matter
    rlo = lo_f[edge_faces].min(axis=0)
    rhi = hi_f[edge_faces].max(axis=0)
    cand = np.flatnonzero(np.all((lo_f <= rhi) & (hi_f >= rlo), axis=1))
    origin = lo_f[cand].min(axis=0)  # cand always contains the edge faces themselves
    ilo = np.floor((lo_f[cand] - origin) / cell).astype(np.int64)
    ihi = np.floor((hi_f[cand] - origin) / cell).astype(np.int64)
    span = ihi - ilo + 1
    counts = span.prod(axis=1)
    # faces much larger than the grid cell (slivers, big flat panels) are paired by a direct
    # bounding-box query instead of being registered in hundreds of cells
    big = counts > 512
    extra_a: list[np.ndarray] = []
    extra_b: list[np.ndarray] = []
    if big.any():
        efs = edge_faces
        for bf in cand[big]:
            ov = np.flatnonzero(np.all((lo_f[efs] <= hi_f[bf]) & (hi_f[efs] >= lo_f[bf]), axis=1))
            extra_a.append(efs[ov])
            extra_b.append(np.full(len(ov), bf))
            if bf in set(edge_faces.tolist()):
                ov2 = np.flatnonzero(np.all((lo_f[cand] <= hi_f[bf]) & (hi_f[cand] >= lo_f[bf]), axis=1))
                extra_a.append(np.full(len(ov2), bf))
                extra_b.append(cand[ov2])
        cand, ilo, span, counts = cand[~big], ilo[~big], span[~big], counts[~big]
        ihi = ilo + span - 1
    total = int(counts.sum())
    if total > max_pairs and len(edge_faces) > 1:
        h = len(edge_faces) // 2
        return _hits_core(tris, faces, lo_f, hi_f, edge_faces[:h], cell) | _hits_core(tris, faces, lo_f, hi_f, edge_faces[h:], cell)
    rep = np.repeat(np.arange(len(cand)), counts)
    offs = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    sp = span[rep]
    ox = offs % sp[:, 0]
    oy = (offs // sp[:, 0]) % sp[:, 1]
    oz = offs // (sp[:, 0] * sp[:, 1])
    cx, cy, cz = ilo[rep, 0] + ox, ilo[rep, 1] + oy, ilo[rep, 2] + oz
    # exact scalar cell key (no hash collisions): linear index in the candidate grid
    dims = ihi.max(axis=0) + 1
    raw_key = (cx * dims[1] + cy) * dims[2] + cz
    order = np.argsort(raw_key, kind="stable")
    rk = raw_key[order]
    face_s = cand[rep[order]]
    newgrp = np.ones(len(rk), dtype=bool)
    newgrp[1:] = rk[1:] != rk[:-1]
    key_s = np.cumsum(newgrp) - 1
    gstart = np.flatnonzero(newgrp)
    glen = np.diff(np.append(gstart, len(rk)))
    is_edge = np.zeros(len(faces), dtype=bool)
    is_edge[edge_faces] = True
    epos = np.flatnonzero(is_edge[face_s])
    g = key_s[epos]
    cnt = glen[g]
    n_pairs = int(cnt.sum())
    if n_pairs > max_pairs and len(edge_faces) > 1:
        h = len(edge_faces) // 2
        return _hits_core(tris, faces, lo_f, hi_f, edge_faces[:h], cell) | _hits_core(tris, faces, lo_f, hi_f, edge_faces[h:], cell)
    A = np.repeat(face_s[epos], cnt)
    within = np.arange(n_pairs) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    B = face_s[np.repeat(gstart[g], cnt) + within]
    if extra_a:
        A = np.concatenate([A] + extra_a)
        B = np.concatenate([B] + extra_b)
    k = A != B
    A, B = A[k], B[k]
    ov = np.all((lo_f[A] <= hi_f[B]) & (lo_f[B] <= hi_f[A]), axis=1)
    A, B = A[ov], B[ov]
    if len(A) == 0:
        return set()
    pk = np.unique(A * np.int64(len(faces)) + B)
    pa, pb = pk // len(faces), pk % len(faces)
    fa, fb = faces[pa], faces[pb]
    share = (fa[:, :, None] == fb[:, None, :]).any(axis=(1, 2))
    pa, pb = pa[~share], pb[~share]
    hits: set[tuple[int, int]] = set()
    if len(pa) == 0:
        return hits
    # two triangles intersect iff an edge of either pierces the other: test both directions
    pa, pb = np.concatenate([pa, pb]), np.concatenate([pb, pa])
    T = tris[pb]
    v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    scale = float(np.median(np.linalg.norm(e1, axis=1))) or 1.0
    eps = 1e-10 * scale * scale
    for (i, j) in ((0, 1), (1, 2), (2, 0)):
        o = tris[pa, i]
        dvec = tris[pa, j] - o
        h = np.cross(dvec, e2)
        a = (e1 * h).sum(1)
        ok = np.abs(a) > eps
        f = np.zeros_like(a)
        f[ok] = 1.0 / a[ok]
        s_ = o - v0
        u = f * (s_ * h).sum(1)
        q = np.cross(s_, e1)
        v = f * (dvec * q).sum(1)
        t = f * (e2 * q).sum(1)
        hit = ok & (u > 1e-9) & (v > 1e-9) & (u + v < 1 - 1e-9) & (t > 1e-9) & (t < 1 - 1e-9)
        for x, y in zip(pa[hit].tolist(), pb[hit].tolist()):
            hits.add((min(x, y), max(x, y)))
    return hits


def _genuinely_new(surf: TriSurface, candidates: set[tuple[int, int]], old: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Intersections that are not just the pre-existing intersection curve shifting.

    A pre-existing interference (e.g. tyre/body overlap in a wheel arch) moves across
    neighbouring triangles when nearby geometry deforms slightly.  A candidate pair counts as
    new only if neither of its faces lies within a few edge lengths of a face that already
    intersected in the baseline."""
    if not candidates or not old:
        return candidates
    from scipy.spatial import cKDTree

    old_faces = np.unique(np.array(sorted(old)).ravel())
    cen = surf.points[surf.faces].mean(axis=1)
    e = surf.points[surf.faces[:, 1]] - surf.points[surf.faces[:, 0]]
    radius = 3.0 * float(np.median(np.linalg.norm(e[old_faces], axis=1)))
    tree = cKDTree(cen[old_faces])
    out = set()
    for a, b in candidates:
        da, _ = tree.query(cen[a])
        db, _ = tree.query(cen[b])
        if da > radius and db > radius:
            out.add((a, b))
    return out


def check_deformation(
    base: TriSurface,
    res: DeformResult,
    parameters: list[GeometryParameter],
    ground_z: Optional[float] = None,
    min_ground_clearance: float = 0.0,
    fold_angle_deg: float = 90.0,
    quality_drop: float = 0.1,
    check_intersections: bool = True,
    components: Optional[list[GeometryComponent]] = None,
) -> DeformCheck:
    """Validate a deformed surface against its baseline.  Errors make the design invalid.

    With ``components`` given, a component that ends up entirely (or partly) *inside*
    another closed component is also detected — a case surface intersection tests miss."""
    chk = DeformCheck(valid=True)
    disp = np.linalg.norm(res.displacement, axis=1)
    moved_v = disp > 1e-12
    moved_f = moved_v[base.faces].any(axis=1)
    chk.stats = {"max_displacement": float(disp.max()) if len(disp) else 0.0, "moved_vertices": int(moved_v.sum()),
                 "moved_faces": int(moved_f.sum()), "per_parameter_max": res.per_parameter_max, "pinned_vertices": res.pinned_vertices}
    if not moved_f.any():
        return chk
    for p in parameters:
        lim = p.max_displacement
        got = res.per_parameter_max.get(p.id, 0.0)
        if lim is not None and got > lim * (1 + 1e-9):
            chk.errors.append({"code": "geometry.displacement_limit", "parameter": p.id,
                               "message": f"Parameter '{p.name}' moves the surface by {got * 1000:.1f} mm, above its {lim * 1000:.1f} mm limit."})
    t0 = base.points[base.faces[moved_f]]
    t1 = res.surface.points[base.faces[moved_f]]
    n0 = np.cross(t0[:, 1] - t0[:, 0], t0[:, 2] - t0[:, 0])
    n1 = np.cross(t1[:, 1] - t1[:, 0], t1[:, 2] - t1[:, 0])
    a0 = np.linalg.norm(n0, axis=1)
    a1 = np.linalg.norm(n1, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cosang = (n0 * n1).sum(1) / (a0 * a1)
    cosang = np.nan_to_num(cosang, nan=1.0)
    folded = int((cosang < math.cos(math.radians(fold_angle_deg))).sum())
    collapsed = int(((a1 < 1e-6 * np.maximum(a0, 1e-30)) & (a0 > 0)).sum())
    q0, q1 = _tri_quality(t0), _tri_quality(t1)
    degraded = int(((q1 < quality_drop * q0) & (q0 > 0.05)).sum())
    chk.stats.update({"folded_faces": folded, "collapsed_faces": collapsed, "degraded_faces": degraded,
                      "min_quality_moved": float(q1.min()) if len(q1) else None})
    if folded:
        chk.errors.append({"code": "geometry.folded", "message": f"{folded} triangle(s) flipped orientation: the deformation folds the surface."})
    if collapsed:
        chk.errors.append({"code": "geometry.collapsed", "message": f"{collapsed} triangle(s) collapsed to zero area."})
    if degraded:
        chk.warnings.append({"code": "geometry.stretched", "message": f"{degraded} triangle(s) lost more than {100 * (1 - quality_drop):.0f}% of their shape quality; "
                             "snappyHexMesh will still snap to them but feature capture may degrade."})
    if res.pinned_vertices:
        chk.warnings.append({"code": "geometry.pinned", "message": f"{res.pinned_vertices} vertex/vertices shared with frozen components were held fixed; "
                             "check for a crease where the deformed part meets a frozen part."})
    if check_intersections:
        edge_faces = np.flatnonzero(moved_f)
        new_hits = edge_triangle_hits(res.surface.points, base.faces, edge_faces)
        if new_hits:
            old_hits = edge_triangle_hits(base.points, base.faces, edge_faces)
            new_hits = _genuinely_new(res.surface, new_hits - old_hits, old_hits)
        chk.stats["new_intersections"] = len(new_hits)
        if new_hits:
            regs = sorted({base.region_names[base.regions[i]] for pair in list(new_hits)[:200] for i in pair})
            chk.errors.append({"code": "geometry.self_intersection",
                               "message": f"The deformed surface intersects itself ({len(new_hits)} new triangle intersections; regions: {', '.join(regs)})."})
    if ground_z is not None:
        zmin = float(res.surface.points[moved_v, 2].min())
        chk.stats["moved_min_z"] = zmin
        if zmin < ground_z + min_ground_clearance:
            chk.errors.append({"code": "geometry.ground_contact",
                               "message": f"A deformed part reaches z = {zmin:.4f} m, at or below the ground plane (z = {ground_z:.4f} m)."})
    if components and check_intersections:
        enclosed = _new_containment(base, res, components, moved_v)
        chk.stats["enclosed_pairs"] = enclosed
        for a, b, frac in enclosed:
            chk.errors.append({"code": "geometry.enclosed",
                               "message": f"{frac * 100:.0f}% of component '{a}' lies inside component '{b}' after the deformation."})
    chk.valid = not chk.errors
    return chk


def _closed(faces: np.ndarray) -> bool:
    e = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, cnt = unique_int_rows(e, return_counts=True)
    return bool(len(cnt)) and bool(np.all(cnt == 2))


def points_inside(points: np.ndarray, faces: np.ndarray, query: np.ndarray, rng_seed: int = 0) -> np.ndarray:
    """Ray-parity point-in-solid test for a closed triangle surface (majority of 3 rays)."""
    tris = points[faces]
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    rng = np.random.default_rng(rng_seed)
    dirs = [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])]
    votes = np.zeros(len(query), dtype=int)
    for k, d0 in enumerate(dirs):
        d = d0 + rng.normal(0, 1e-4, 3)
        d /= np.linalg.norm(d)
        a1, a2 = [i for i in range(3) if i != k]
        for qi, q in enumerate(query):
            m = (lo[:, a1] <= q[a1] + 1e-9) & (hi[:, a1] >= q[a1] - 1e-9) & (lo[:, a2] <= q[a2] + 1e-9) & (hi[:, a2] >= q[a2] - 1e-9) & (hi[:, k] >= q[k])
            if not m.any():
                continue
            T = tris[m]
            e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
            h = np.cross(np.broadcast_to(d, e2.shape), e2)
            a = (e1 * h).sum(1)
            ok = np.abs(a) > 1e-14
            f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
            sv = q - T[:, 0]
            u = f * (sv * h).sum(1)
            qv = np.cross(sv, e1)
            v = f * (qv @ d)
            t = f * (e2 * qv).sum(1)
            hits = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-12)
            votes[qi] += int(hits.sum()) % 2
    return votes >= 2


def _new_containment(base: TriSurface, res: DeformResult, components: list[GeometryComponent], moved_v: np.ndarray,
                     n_sample: int = 120, threshold: float = 0.25) -> list[tuple[str, str, float]]:
    """(inner, outer, fraction) pairs where a component newly lies inside a closed one."""
    name_to_idx = {n: i for i, n in enumerate(base.region_names)}
    fmasks = {}
    for c in components:
        ridx = [name_to_idx[r] for r in c.source_regions if r in name_to_idx]
        fm = np.isin(base.regions, ridx)
        if fm.any():
            fmasks[c.id] = fm
    moved_comps = {cid for cid, fm in fmasks.items() if moved_v[np.unique(base.faces[fm])].any()}
    if not moved_comps:
        return []
    rng = np.random.default_rng(0)
    out = []
    for a in fmasks:
        for b in fmasks:
            if a == b or (a not in moved_comps and b not in moved_comps):
                continue
            fb = base.faces[fmasks[b]]
            if not _closed(fb):
                continue
            va = np.unique(base.faces[fmasks[a]])
            sample = va if len(va) <= n_sample else rng.choice(va, n_sample, replace=False)
            new_in = points_inside(res.surface.points, fb, res.surface.points[sample]).mean()
            if new_in < threshold:
                continue
            old_in = points_inside(base.points, fb, base.points[sample]).mean()
            if new_in - old_in >= threshold:
                out.append((a, b, float(new_in)))
    return out


def parameter_values_complete(parameters: Iterable[GeometryParameter], values: dict[str, float]) -> dict[str, float]:
    """All parameters with explicit values (baseline where not given)."""
    out = {p.id: float(p.baseline) for p in parameters}
    for k, v in values.items():
        if k not in out:
            raise ValueError(f"unknown geometry parameter '{k}'")
        out[k] = float(v)
    return out
