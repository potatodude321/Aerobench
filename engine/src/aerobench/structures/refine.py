"""Make a CAD tessellation usable as a shell mesh without changing its shape.

CAD exports are made for display: long thin triangles along flat or gently curved faces and
fans of slivers on discs. Shell elements tolerate long thin shapes (see ``mesh``) but need
to be short enough along the direction the part bends. Two shape-preserving operations:

* every edge longer than the target size is divided into equal segments (the new points lie
  on the edge, so on the surface), and each triangle is re-triangulated in its own plane
  from its divided boundary — no interior points, so a long strip becomes a zip of
  triangles rather than the fans repeated bisection would leave; two triangles that form a
  flat quad across their longest edge (a CAD strip and its diagonal) are re-triangulated
  as one quad, so no slivers line the old diagonal; the target grows until the result fits
  the element budget;
* the diagonal of two neighbouring triangles is flipped when that makes the pair Delaunay
  (larger smallest angle), provided they are not across a feature edge (``flip_angle``) and
  the surface moves by no more than ``tolerance`` (distance between the old and the new
  diagonal) — about a CAD tolerance, so flips happen on flat patches only.

Parts never merge (edges are only divided, and flips need both triangles in the same region).
"""

from __future__ import annotations

import math

import numpy as np

from ..geometry.fastops import unique_int_rows


def _edges(faces: np.ndarray):
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key = np.sort(e, axis=1)
    uniq, inv = unique_int_rows(key, return_inverse=True)
    m = len(faces)
    return uniq, np.stack([inv[:m], inv[m:2 * m], inv[2 * m:]], axis=1)  # face edge k joins vertex k and k+1


def _segments(L: np.ndarray, h: float) -> np.ndarray:
    """Segments per edge for target size h (an edge up to 1.25 h stays whole)."""
    return np.maximum(1, np.ceil(L / h - 0.25)).astype(np.int64)


def _clip(xy: np.ndarray, eps: float) -> list[tuple[int, int, int]]:
    """Triangulate a convex polygon (2D, counter-clockwise, collinear points allowed) without
    interior points, clipping the best-shaped valid ear each time. Returns local indices."""
    idx = list(range(len(xy)))
    out: list[tuple[int, int, int]] = []

    def area2(a, b, c):
        return (xy[b, 0] - xy[a, 0]) * (xy[c, 1] - xy[a, 1]) - (xy[b, 1] - xy[a, 1]) * (xy[c, 0] - xy[a, 0])

    def quality(a, b, c):
        d = (xy[a] - xy[b]) @ (xy[a] - xy[b]) + (xy[b] - xy[c]) @ (xy[b] - xy[c]) + (xy[c] - xy[a]) @ (xy[c] - xy[a])
        return 2 * math.sqrt(3) * area2(a, b, c) / max(d, 1e-300)

    total = sum(area2(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1))
    while len(idx) > 3:
        best, best_q = -1, -1.0
        n = len(idx)
        for k in range(n):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % n]
            ar = area2(a, b, c)
            if ar <= eps or total - ar <= eps:  # degenerate ear, or what is left would be degenerate
                continue
            q = quality(a, b, c)
            if q > best_q:
                best, best_q = k, q
        if best < 0:
            raise ValueError("polygon cannot be triangulated")
        a, b, c = idx[best - 1], idx[best], idx[(best + 1) % n]
        out.append((a, b, c))
        total -= area2(a, b, c)
        del idx[best]
    out.append((idx[0], idx[1], idx[2]))
    return out


def _face_pairs(F: np.ndarray, fe: np.ndarray):
    """(f1, s1, f2, s2) for every edge shared by exactly two faces: the edge is at slot s1 of
    f1 (running a -> b) and at slot s2 of f2 (running b -> a)."""
    m = len(F)
    flat_e = fe.reshape(-1)
    face_of = np.repeat(np.arange(m), 3)
    slot = np.tile(np.arange(3), m)
    order = np.argsort(flat_e, kind="stable")
    se = flat_e[order]
    starts = np.flatnonzero(np.r_[True, se[1:] != se[:-1]])
    counts = np.diff(np.r_[starts, len(se)])
    two = starts[counts == 2]
    return face_of[order[two]], slot[order[two]], face_of[order[two + 1]], slot[order[two + 1]], se[two]


def _quads(P: np.ndarray, F: np.ndarray, R: np.ndarray, fe: np.ndarray, L: np.ndarray, tol: float):
    """Pairs of triangles that form a flat convex quad across the longest edge of both — the
    diagonal of a CAD strip. Dividing the quad's sides and re-triangulating the quad as a
    whole avoids slivers along that diagonal. Each face is in at most one pair."""
    f1, s1, f2, s2, e = _face_pairs(F, fe)
    ok = (R[f1] == R[f2]) & (np.argmax(L[fe[f1]], axis=1) == s1) & (np.argmax(L[fe[f2]], axis=1) == s2)
    f1, s1, f2, s2, e = f1[ok], s1[ok], f2[ok], s2[ok], e[ok]
    a, b = F[f1, s1], F[f1, (s1 + 1) % 3]
    c, d = F[f1, (s1 + 2) % 3], F[f2, (s2 + 2) % 3]
    n1 = np.cross(P[b] - P[a], P[c] - P[a])
    cr = np.cross(P[b] - P[a], P[d] - P[c])
    flat = np.abs(((P[c] - P[a]) * cr).sum(1)) <= tol * np.maximum(np.linalg.norm(cr, axis=1), 1e-300)
    convex = ((np.cross(P[d] - P[a], P[c] - P[a]) * n1).sum(1) > 0) & ((np.cross(P[b] - P[d], P[c] - P[d]) * n1).sum(1) > 0)
    sel = np.flatnonzero(flat & convex & (c != d))
    used = np.zeros(len(F), dtype=bool)
    keep = []
    for k in sel[np.argsort(-L[e[sel]], kind="stable")]:
        if used[f1[k]] or used[f2[k]]:
            continue
        used[f1[k]] = used[f2[k]] = True
        keep.append(k)
    k = np.asarray(keep, dtype=np.int64)
    return f1[k], s1[k], f2[k], s2[k], e[k]


def subdivide(points: np.ndarray, faces: np.ndarray, regions: np.ndarray, target: float, max_faces: int, tol: float = 1e-5):
    """Divide long edges into equal segments and re-triangulate the faces (or flat quads of
    two faces) they bound. Returns (points, faces, regions, size used, budget reached)."""
    P, F, R = np.asarray(points, float), np.asarray(faces, np.int64), np.asarray(regions)
    uniq, fe = _edges(F)
    L = np.linalg.norm(P[uniq[:, 0]] - P[uniq[:, 1]], axis=1)
    q1, qs1, q2, qs2, qe = _quads(P, F, R, fe, L, tol)
    in_quad = np.zeros(len(F), dtype=bool)
    in_quad[q1] = in_quad[q2] = True
    tri = np.flatnonzero(~in_quad)
    # boundary edges of every polygon: triangles (3 edges), quads (4 edges, counter-clockwise from a)
    # quad a -> d -> b -> c with f1 = (a, b, c) and f2 = (b, a, d)
    poly_edges = [fe[tri]]  # (T, 3)
    quad_edges = np.stack([fe[q2, (qs2 + 1) % 3], fe[q2, (qs2 + 2) % 3], fe[q1, (qs1 + 1) % 3], fe[q1, (qs1 + 2) % 3]], axis=1)
    diag = np.zeros(len(uniq), dtype=bool)
    diag[qe] = True

    def total(h: float) -> int:  # a polygon with V boundary vertices gives V - 2 triangles
        n = _segments(L, h)
        return int(n[poly_edges[0]].sum() - 2 * len(tri) + n[quad_edges].sum() - 2 * len(q1))

    h, budget = float(target), False
    if total(h) > max_faces:
        budget = True
        lo, hi = h, float(L.max()) * 1.25 + 1e-12
        if total(hi) > max_faces:
            return P, F, R, hi, True
        for _ in range(60):
            mid = math.sqrt(lo * hi)
            if total(mid) <= max_faces:
                hi = mid
            else:
                lo = mid
        h = hi
    n = _segments(L, h)
    n[diag] = 1  # quad diagonals disappear
    if np.all(n == 1):
        return P, F, R, h, budget
    # new points on the divided edges, in order from uniq[:, 0] to uniq[:, 1]
    extra = n - 1
    first = len(P) + np.r_[0, np.cumsum(extra)[:-1]]
    e_of = np.repeat(np.arange(len(uniq)), extra)
    k_of = np.arange(int(extra.sum())) - np.repeat(np.cumsum(extra) - extra, extra) + 1
    t = (k_of / n[e_of])[:, None]
    P2 = np.vstack([P, P[uniq[e_of, 0]] * (1 - t) + P[uniq[e_of, 1]] * t])

    keep = np.ones(len(F), dtype=bool)
    nf: list[tuple[int, int, int]] = []
    nr: list = []

    def emit(corners: list[int], edges: list[int], normal_face: int, region) -> None:
        poly: list[int] = []
        for k, a in enumerate(corners):
            e = edges[k]
            poly.append(a)
            ids = list(range(int(first[e]), int(first[e] + extra[e])))
            if int(uniq[e, 0]) != a:
                ids.reverse()
            poly += ids
        X = P2[poly]
        o = X[0]
        f = F[normal_face]
        e1 = P[f[1]] - P[f[0]]
        nrm = np.cross(e1, P[f[2]] - P[f[0]])
        e1 = e1 / np.linalg.norm(e1)
        e2 = np.cross(nrm / np.linalg.norm(nrm), e1)  # the face's plane, counter-clockwise like the face
        xy = np.stack([(X - o) @ e1, (X - o) @ e2], axis=1)
        scale = float(np.ptp(xy, axis=0).max()) ** 2
        for a, b, c in _clip(xy, 1e-9 * scale):
            nf.append((poly[a], poly[b], poly[c]))
            nr.append(region)

    for f in tri[(n[fe[tri]] > 1).any(axis=1)]:
        keep[f] = False
        emit([int(v) for v in F[f]], [int(e) for e in fe[f]], int(f), R[f])
    for k in np.flatnonzero((n[quad_edges] > 1).any(axis=1)):
        f1, f2 = int(q1[k]), int(q2[k])
        a, b = int(F[f1, qs1[k]]), int(F[f1, (qs1[k] + 1) % 3])
        c, d = int(F[f1, (qs1[k] + 2) % 3]), int(F[f2, (qs2[k] + 2) % 3])
        keep[f1] = keep[f2] = False
        emit([a, d, b, c], [int(e) for e in quad_edges[k]], f1, R[f1])
    out_f, out_r = [F[keep]], [R[keep]]
    if nf:
        out_f.append(np.asarray(nf, dtype=np.int64))
        out_r.append(np.asarray(nr, dtype=R.dtype))
    return P2, np.vstack(out_f), np.concatenate(out_r), h, budget


def _angles(P: np.ndarray, f: np.ndarray) -> np.ndarray:
    a, b, c = P[f[:, 0]], P[f[:, 1]], P[f[:, 2]]

    def ang(x, y, z):  # angle at x
        u, w = y - x, z - x
        return np.arctan2(np.linalg.norm(np.cross(u, w), axis=1), (u * w).sum(1))

    return np.stack([ang(a, b, c), ang(b, c, a), ang(c, a, b)], axis=1)


def _flip_pass(P: np.ndarray, faces: np.ndarray, regions: np.ndarray, cos_flat: float, tol: float) -> int:
    """One pass of Delaunay flips (in place). Returns the number of flips."""
    uniq, fe = _edges(faces)
    m = len(faces)
    flat_e = fe.reshape(-1)
    face_of = np.repeat(np.arange(m), 3)
    slot = np.tile(np.arange(3), m)
    order = np.argsort(flat_e, kind="stable")
    se = flat_e[order]
    starts = np.flatnonzero(np.r_[True, se[1:] != se[:-1]])
    counts = np.diff(np.r_[starts, len(se)])
    two = starts[counts == 2]
    if not len(two):
        return 0
    f1, s1 = face_of[order[two]], slot[order[two]]
    f2, s2 = face_of[order[two + 1]], slot[order[two + 1]]
    same = regions[f1] == regions[f2]
    f1, s1, f2, s2 = f1[same], s1[same], f2[same], s2[same]
    n = np.cross(P[faces[:, 1]] - P[faces[:, 0]], P[faces[:, 2]] - P[faces[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
    flat = (n[f1] * n[f2]).sum(1) > cos_flat
    f1, s1, f2, s2 = f1[flat], s1[flat], f2[flat], s2[flat]
    if not len(f1):
        return 0
    # face 1 = (a, b, c) with the shared edge a->b at slot s1; face 2 has b->a
    ar = np.arange(len(f1))
    a = faces[f1, s1]
    b = faces[f1, (s1 + 1) % 3]
    c = faces[f1, (s1 + 2) % 3]
    d = faces[f2, (s2 + 2) % 3]
    ang1 = _angles(P, faces[f1])[ar, (s1 + 2) % 3]
    ang2 = _angles(P, faces[f2])[ar, (s2 + 2) % 3]
    want = ang1 + ang2 > math.pi + 1e-9
    # the new pair (a, d, c), (d, b, c) must keep the orientation (convex quad)
    na = np.cross(P[d] - P[a], P[c] - P[a])
    nb = np.cross(P[b] - P[d], P[c] - P[d])
    # how far the surface moves: the distance between the old and the new diagonal (0 when the
    # two triangles are coplanar)
    cr = np.cross(P[b] - P[a], P[d] - P[c])
    moved = np.abs(((P[c] - P[a]) * cr).sum(1)) / np.maximum(np.linalg.norm(cr, axis=1), 1e-300)
    ok = want & ((na * n[f1]).sum(1) > 0) & ((nb * n[f1]).sum(1) > 0) & (c != d) & (moved <= tol)
    # gain: smallest angle before vs after
    cand = np.flatnonzero(ok)
    if not len(cand):
        return 0
    newf1 = np.stack([a, d, c], 1)[cand]
    newf2 = np.stack([d, b, c], 1)[cand]
    before = np.minimum(_angles(P, faces[f1[cand]]).min(1), _angles(P, faces[f2[cand]]).min(1))
    after = np.minimum(_angles(P, newf1).min(1), _angles(P, newf2).min(1))
    better = after > before + 1e-9
    cand, newf1, newf2 = cand[better], newf1[better], newf2[better]
    if not len(cand):
        return 0
    # an independent set: each face takes part in one flip per pass
    used = np.zeros(m, dtype=bool)
    gain = (after - before)[better]
    flips = 0
    for i in np.argsort(-gain):
        k = cand[i]
        if used[f1[k]] or used[f2[k]]:
            continue
        used[f1[k]] = used[f2[k]] = True
        faces[f1[k]] = newf1[i]
        faces[f2[k]] = newf2[i]
        flips += 1
    return flips


def improve(points: np.ndarray, faces: np.ndarray, regions: np.ndarray, target: float, max_faces: int,
            flip_angle_deg: float = 25.0, tolerance: float = 1e-5) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Divide edges longer than about ``target`` (a larger size if the budget ``max_faces``
    requires), then swap diagonals towards Delaunay where the surface moves by at most
    ``tolerance``."""
    n0 = len(faces)
    P, F, R, h, budget = subdivide(points, faces, regions, target, max_faces, tol=tolerance)
    F = F.copy()
    cos_flat = math.cos(math.radians(flip_angle_deg))
    flips = 0
    for _ in range(30):
        k = _flip_pass(P, F, R, cos_flat, tolerance)
        flips += k
        if k == 0:
            break
    return P, F, R, {"faces_in": n0, "faces_out": int(len(F)), "size": h, "flips": flips, "target": target,
                     "tolerance": tolerance, "budget_reached": budget}
