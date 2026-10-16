"""Feature-preserving, curvature-adaptive isotropic remeshing for the structural model.

Production meshes are made for the flow or for display: very fine where the flow needs it,
with slivers and tiny triangles where CAD faces meet. A shell model needs the opposite:
well-shaped triangles of about the element size everywhere, smaller only where the surface
curves tightly. CalculiX expands every shell triangle into a wedge through the laminate
thickness along nodal normals; a sliver much thinner than the laminate, next to a kink of
the tessellation, folds over in that expansion ("nonpositive jacobian").

This is the incremental remeshing of Botsch & Kobbelt (2004) with a sizing field, vectorised:

1. split edges longer than 4/3 of the local size at their midpoint;
2. collapse edges shorter than 4/5 of it (link condition; no face may turn over or get an
   edge longer than the split threshold);
3. flip edges towards valence 6;
4. move every vertex towards the centre of its neighbourhood in the tangent plane;

every new or moved vertex is projected back onto the *original* surface, so the shape does not
drift. Sharp edges of the original (dihedral angle above ``feature_angle``, unless they only
form a short fragment, as tessellation noise does), open boundaries and the borders between
parts are features: their vertices stay on the original feature lines, feature edges are never
flipped and only collapse along themselves; vertices where features meet (corners) never move.
Parts never merge. The local size follows the curvature: where neighbouring triangles meet at
more than ``facet_angle`` the size there shrinks (down to ``h_min``) so that curved surfaces are
followed closely; the size field is scaled up when it would exceed the element budget.

Each pass works on an independent set of operations (the endpoints of two simultaneous
collapses are at least two edges apart), so the result does not depend on triangle order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.spatial import cKDTree

from ..geometry.fastops import unique_int_rows

REGULAR, FEATURE, CORNER = 0, 1, 2
KEYBASE = np.int64(1) << np.int64(31)  # vertex ids stay below this; edge key = lo * KEYBASE + hi
EQ_AREA = math.sqrt(3.0) / 4.0  # area of an equilateral triangle of unit edge
FILL = 1.2  # triangles of a remeshed surface per triangle of an equilateral tiling at the target size


# ---------------------------------------------------------------------------- topology helpers


def _edges(F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unique edges (sorted pairs) and the edge of each face side (side k joins corner k and k+1)."""
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    uniq, inv = unique_int_rows(np.sort(e, axis=1), return_inverse=True)
    m = len(F)
    return uniq, np.stack([inv[:m], inv[m:2 * m], inv[2 * m:]], axis=1)


def _edge_faces(fe: np.ndarray, n_edges: int) -> tuple[np.ndarray, np.ndarray]:
    """(E, 2) faces on each edge (-1 when missing) and the number of faces per edge."""
    flat = fe.reshape(-1)
    face = np.repeat(np.arange(len(fe)), 3)
    order = np.argsort(flat, kind="stable")
    cnt = np.bincount(flat, minlength=n_edges)
    start = np.r_[0, np.cumsum(cnt)[:-1]]
    ef = -np.ones((n_edges, 2), dtype=np.int64)
    has1 = cnt >= 1
    ef[has1, 0] = face[order[start[has1]]]
    has2 = cnt >= 2
    ef[has2, 1] = face[order[start[has2] + 1]]
    return ef, cnt


def _keys(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    return np.minimum(a, b) * KEYBASE + np.maximum(a, b)


def _unkey(k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return (k // KEYBASE).astype(np.int64), (k % KEYBASE).astype(np.int64)


def _csr(rows: np.ndarray, cols: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(rows, kind="stable")
    ptr = np.r_[0, np.cumsum(np.bincount(rows, minlength=n))]
    return ptr, cols[order]


def _expand(ptr: np.ndarray, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each i in ``ids`` the positions ptr[i]..ptr[i+1]: returns (index into ids, position)."""
    cnt = ptr[ids + 1] - ptr[ids]
    total = int(cnt.sum())
    rep = np.repeat(np.arange(len(ids)), cnt)
    pos = np.repeat(ptr[ids] - (np.cumsum(cnt) - cnt), cnt) + np.arange(total)
    return rep, pos


def _face_normals(P: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-300)[:, None], 0.5 * a


def _vertex_normals(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    n, a = _face_normals(P, F)
    vn = np.zeros_like(P)
    w = n * a[:, None]
    for k in range(3):
        vn += np.stack([np.bincount(F[:, k], weights=w[:, d], minlength=len(P)) for d in range(3)], axis=1)
    return vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-300)[:, None]


def _vertex_region(F: np.ndarray, R: np.ndarray, n: int) -> np.ndarray:
    vr = np.zeros(n, dtype=R.dtype)
    for k in range(3):
        vr[F[:, k]] = R
    return vr


def quality(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Shape quality 4*sqrt(3)*A / sum(l^2) per triangle: 1 = equilateral, 0 = degenerate."""
    t = P[F]
    e = np.linalg.norm(t - np.roll(t, -1, axis=1), axis=2)
    a = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    return 4 * math.sqrt(3) * a / np.maximum((e ** 2).sum(1), 1e-300)


def closest_on_triangles(Q: np.ndarray, A: np.ndarray, B: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Closest points of triangles (A, B, C) to points Q, all (n, 3) (Ericson 5.1.5, vectorised)."""
    ab, ac, ap = B - A, C - A, Q - A
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    bp = Q - B
    d3, d4 = np.einsum("ij,ij->i", ab, bp), np.einsum("ij,ij->i", ac, bp)
    cp = Q - C
    d5, d6 = np.einsum("ij,ij->i", ab, cp), np.einsum("ij,ij->i", ac, cp)
    vc, vb, va = d1 * d4 - d3 * d2, d5 * d2 - d1 * d6, d3 * d6 - d5 * d4
    with np.errstate(divide="ignore", invalid="ignore"):
        den = va + vb + vc
        v = np.where(den != 0, vb / den, 0.0)
        w = np.where(den != 0, vc / den, 0.0)
        # regions, in Ericson's order (the first that applies wins)
        r_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        t_ab = np.where(d1 - d3 != 0, d1 / (d1 - d3), 0.0)
        r_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        t_ac = np.where(d2 - d6 != 0, d2 / (d2 - d6), 0.0)
        r_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        t_bc = np.where((d4 - d3) + (d5 - d6) != 0, (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
    out = A + v[:, None] * ab + w[:, None] * ac
    out = np.where(r_bc[:, None], B + t_bc[:, None] * (C - B), out)
    out = np.where(r_ac[:, None], A + t_ac[:, None] * ac, out)
    out = np.where(((d6 >= 0) & (d5 <= d6))[:, None], C, out)
    out = np.where(r_ab[:, None], A + t_ab[:, None] * ab, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[:, None], B, out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[:, None], A, out)
    return out


# ---------------------------------------------------------------------------- the original surface


def detect_features(P: np.ndarray, F: np.ndarray, R: np.ndarray, angle_deg: float, min_length: float = 0.0) -> np.ndarray:
    """Feature edges as sorted vertex pairs: open and non-manifold edges, borders between parts,
    and sharp edges (dihedral above ``angle_deg``) that form lines of at least ``min_length``."""
    uniq, fe = _edges(F)
    ef, cnt = _edge_faces(fe, len(uniq))
    n, _ = _face_normals(P, F)
    hard = cnt != 2
    sharp = np.zeros(len(uniq), dtype=bool)
    two = np.flatnonzero(cnt == 2)
    f1, f2 = ef[two, 0], ef[two, 1]
    hard[two] = R[f1] != R[f2]
    sharp[two] = np.einsum("ij,ij->i", n[f1], n[f2]) < math.cos(math.radians(angle_deg))
    sharp &= ~hard
    if min_length > 0 and sharp.any():
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components

        s = np.flatnonzero(sharp)
        g = coo_matrix((np.ones(len(s)), (uniq[s, 0], uniq[s, 1])), shape=(len(P), len(P)))
        _, lab = connected_components(g, directed=False)
        L = np.linalg.norm(P[uniq[s, 0]] - P[uniq[s, 1]], axis=1)
        length = np.bincount(lab[uniq[s, 0]], weights=L, minlength=lab.max() + 1)
        sharp[s] = length[lab[uniq[s, 0]]] >= min_length
    return uniq[hard | sharp]


class _Grid:
    """Uniform grid of cells holding every primitive whose (slightly enlarged) bounding box
    overlaps them: the candidates for a point are the primitives of its cell, so the closest
    one is found whatever the primitives' shape (slivers, long strips)."""

    def __init__(self, lo_box: np.ndarray, hi_box: np.ndarray, cell: float, margin: float):
        self.cell = float(cell)
        self.origin = np.minimum(lo_box.min(axis=0), hi_box.min(axis=0)) - margin - self.cell
        top = np.maximum(lo_box.max(axis=0), hi_box.max(axis=0)) + margin + self.cell
        self.dims = np.maximum(np.ceil((top - self.origin) / self.cell).astype(np.int64), 1)
        i0 = np.floor((lo_box - margin - self.origin) / self.cell).astype(np.int64)
        i1 = np.floor((hi_box + margin - self.origin) / self.cell).astype(np.int64)
        span = i1 - i0 + 1
        cnt = span.prod(axis=1)
        prim = np.repeat(np.arange(len(lo_box)), cnt)
        local = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        sp = span[prim]
        ix = i0[prim, 0] + local % sp[:, 0]
        iy = i0[prim, 1] + (local // sp[:, 0]) % sp[:, 1]
        iz = i0[prim, 2] + local // (sp[:, 0] * sp[:, 1])
        key = (iz * self.dims[1] + iy) * self.dims[0] + ix
        order = np.argsort(key, kind="stable")
        self.keys, start = np.unique(key[order], return_index=True)
        self.start = np.r_[start, len(key)]
        self.prim = prim[order]

    def candidates(self, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(query index, primitive) pairs for the primitives in each query point's cell."""
        i = np.clip(np.floor((Q - self.origin) / self.cell).astype(np.int64), 0, self.dims - 1)
        key = (i[:, 2] * self.dims[1] + i[:, 1]) * self.dims[0] + i[:, 0]
        j = np.searchsorted(self.keys, key)
        j = np.minimum(j, len(self.keys) - 1)
        found = self.keys[j] == key
        s0 = np.where(found, self.start[j], 0)
        s1 = np.where(found, self.start[j + 1], 0)
        cnt = s1 - s0
        rep = np.repeat(np.arange(len(Q)), cnt)
        pos = np.repeat(s0 - (np.cumsum(cnt) - cnt), cnt) + np.arange(int(cnt.sum()))
        return rep, self.prim[pos]


def _closest_per_query(n: int, rep: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """For each query (``rep`` ascending) the index of its candidate with the smallest distance, -1 if none."""
    best = np.full(n, -1, dtype=np.int64)
    if not len(rep):
        return best
    starts = np.flatnonzero(np.r_[True, rep[1:] != rep[:-1]])
    gmin = np.minimum.reduceat(dist, starts)
    group = np.cumsum(np.r_[False, rep[1:] != rep[:-1]])
    is_min = dist == gmin[group]
    first = np.flatnonzero(is_min & np.r_[True, group[1:] != group[:-1]] | is_min)
    # the first minimum of each group
    g = group[first]
    keep = np.r_[True, g[1:] != g[:-1]]
    best[rep[first[keep]]] = first[keep]
    return best


class Reference:
    """The original surface and its feature lines, for projecting vertices back onto them."""

    def __init__(self, P: np.ndarray, F: np.ndarray, R: np.ndarray, feature_pairs: np.ndarray, cell: Optional[float] = None):
        self.P, self.F, self.R = P, F, R
        self.n, _ = _face_normals(P, F)
        self.A, self.B, self.C = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
        t = P[F]
        if cell is None:
            e = np.linalg.norm(t - np.roll(t, -1, axis=1), axis=2)
            cell = float(np.percentile(e.max(axis=1), 90)) * 2.0 if len(F) else 1.0
        # cells of about a third of the element size, primitives listed with a margin of 0.15 of it:
        # the closest primitive to any point that close to the surface is among its cell's
        self.cell = max(0.35 * float(cell), 1e-9)
        margin = 0.15 * float(cell)
        self.grid = _Grid(t.min(axis=1), t.max(axis=1), self.cell, margin)
        self.S0, self.S1 = P[feature_pairs[:, 0]], P[feature_pairs[:, 1]]
        self.seg_grid = _Grid(np.minimum(self.S0, self.S1), np.maximum(self.S0, self.S1), self.cell, margin) if len(feature_pairs) else None
        self.tree = cKDTree(t.mean(axis=1))  # fallback for points far from the surface

    def surface(self, Q: np.ndarray, qn: np.ndarray, qr: np.ndarray, k: int = 12) -> np.ndarray:
        """Closest points on original faces of the same part whose side agrees with ``qn``."""
        if not len(Q):
            return Q
        rep, tri = self.grid.candidates(Q)
        ok = (self.R[tri] == qr[rep]) & (np.einsum("ij,ij->i", self.n[tri], qn[rep]) > 0.3)
        rep, tri = rep[ok], tri[ok]
        c = closest_on_triangles(Q[rep], self.A[tri], self.B[tri], self.C[tri]) if len(rep) else np.zeros((0, 3))
        d = np.einsum("ij,ij->i", c - Q[rep], c - Q[rep])
        best = _closest_per_query(len(Q), rep, d)
        out = Q.copy()
        hit = best >= 0
        out[hit] = c[best[hit]]
        miss = np.flatnonzero(~hit)
        if len(miss):  # far from the surface (or only the other side near): nearest triangle centres
            kk = min(k, len(self.F))
            _, idx = self.tree.query(Q[miss], k=kk)
            idx = np.asarray(idx).reshape(len(miss), kk)
            bd = np.full(len(miss), np.inf)
            for j in range(kk):
                t = idx[:, j]
                okj = (self.R[t] == qr[miss]) & (np.einsum("ij,ij->i", self.n[t], qn[miss]) > 0.3)
                cj = closest_on_triangles(Q[miss], self.A[t], self.B[t], self.C[t])
                dj = np.linalg.norm(cj - Q[miss], axis=1)
                better = okj & (dj < bd)
                bd[better] = dj[better]
                out[miss[better]] = cj[better]
        return out

    def feature(self, Q: np.ndarray, k: int = 8) -> np.ndarray:
        """Closest points on the original feature lines."""
        if not len(Q) or self.seg_grid is None:
            return Q
        rep, seg = self.seg_grid.candidates(Q)
        a, b = self.S0[seg], self.S1[seg]
        ab = b - a
        t = np.clip(np.einsum("ij,ij->i", Q[rep] - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300), 0.0, 1.0)
        c = a + t[:, None] * ab
        d = np.einsum("ij,ij->i", c - Q[rep], c - Q[rep])
        best = _closest_per_query(len(Q), rep, d)
        out = Q.copy()
        hit = best >= 0
        out[hit] = c[best[hit]]
        return out


# ---------------------------------------------------------------------------- the working mesh


@dataclass
class Mesh:
    P: np.ndarray
    F: np.ndarray
    R: np.ndarray
    feat: np.ndarray  # sorted int64 keys of feature edges
    vtype: np.ndarray  # per vertex: REGULAR, FEATURE or CORNER
    size: np.ndarray  # per vertex: target edge length

    def feature_mask(self, uniq: np.ndarray) -> np.ndarray:
        return np.isin(_keys(uniq[:, 0], uniq[:, 1]), self.feat)


def _vertex_types(n: int, feat_keys: np.ndarray, fixed: Optional[np.ndarray] = None) -> np.ndarray:
    a, b = _unkey(feat_keys)
    deg = np.bincount(np.r_[a, b], minlength=n)
    # where feature lines meet (3 or more feature edges) the vertex stays; the loose end of a line
    # (a crease fading out) may slide along it
    t = np.where(deg == 0, REGULAR, np.where(deg <= 2, FEATURE, CORNER)).astype(np.int8)
    if fixed is not None:
        t[fixed] = CORNER
    return t


def _compact(m: Mesh) -> Mesh:
    used = np.zeros(len(m.P), dtype=bool)
    used[m.F.reshape(-1)] = True
    if used.all():
        return m
    remap = -np.ones(len(m.P), dtype=np.int64)
    remap[used] = np.arange(int(used.sum()))
    a, b = _unkey(m.feat)
    keep = used[a] & used[b]
    feat = np.unique(_keys(remap[a[keep]], remap[b[keep]]))
    return Mesh(m.P[used], remap[m.F], m.R, feat, m.vtype[used], m.size[used])


def _remap_features(feat: np.ndarray, remap: np.ndarray) -> np.ndarray:
    a, b = _unkey(feat)
    ra, rb = remap[a], remap[b]
    ok = ra != rb
    return np.unique(_keys(ra[ok], rb[ok]))


# ---------------------------------------------------------------------------- 1. split


def _split_pass(m: Mesh, ref: Reference) -> tuple[Mesh, int]:
    uniq, fe = _edges(m.F)
    L = np.linalg.norm(m.P[uniq[:, 0]] - m.P[uniq[:, 1]], axis=1)
    tgt = np.minimum(m.size[uniq[:, 0]], m.size[uniq[:, 1]])
    mark = L > 4.0 / 3.0 * tgt
    if not mark.any():
        return m, 0
    isf = m.feature_mask(uniq)
    idx = np.flatnonzero(mark)
    n0 = len(m.P)
    mid_id = -np.ones(len(uniq), dtype=np.int64)
    mid_id[idx] = n0 + np.arange(len(idx))
    ea, eb = uniq[idx, 0], uniq[idx, 1]
    mid = 0.5 * (m.P[ea] + m.P[eb])
    ef, _ = _edge_faces(fe, len(uniq))
    fn, _ = _face_normals(m.P, m.F)
    f1 = ef[idx, 0]
    f2 = np.where(ef[idx, 1] >= 0, ef[idx, 1], f1)
    qn = fn[f1] + fn[f2]
    qn /= np.maximum(np.linalg.norm(qn, axis=1), 1e-300)[:, None]
    fm = isf[idx]
    mid[fm] = ref.feature(mid[fm])
    mid[~fm] = ref.surface(mid[~fm], qn[~fm], m.R[f1[~fm]])
    P = np.vstack([m.P, mid])
    vtype = np.r_[m.vtype, np.where(fm, FEATURE, REGULAR).astype(np.int8)]
    size = np.r_[m.size, 0.5 * (m.size[ea] + m.size[eb])]
    # feature edges: (a, b) -> (a, mid), (mid, b)
    sa, sb, sm = ea[fm], eb[fm], mid_id[idx[fm]]
    feat = m.feat[~np.isin(m.feat, _keys(sa, sb))]
    feat = np.unique(np.r_[feat, _keys(sa, sm), _keys(sm, sb)])
    # conforming templates (1, 2 or 3 split sides per face), orientation kept
    F, R = m.F, m.R
    fmk = mark[fe]
    cnt = fmk.sum(1)
    out_f, out_r = [F[cnt == 0]], [R[cnt == 0]]
    for c in (1, 2, 3):
        sel = np.flatnonzero(cnt == c)
        if not len(sel):
            continue
        f, mk, e, r = F[sel], fmk[sel], fe[sel], R[sel]
        if c == 1:
            rot = np.argmax(mk, axis=1)
        elif c == 2:
            rot = (np.argmin(mk, axis=1) + 1) % 3
        else:
            rot = np.zeros(len(sel), dtype=np.int64)
        ar = np.arange(len(sel))[:, None]
        order = (rot[:, None] + np.arange(3)[None, :]) % 3
        v = f[ar, order]
        me = mid_id[e[ar, order]]
        if c == 1:
            out_f += [np.stack([v[:, 0], me[:, 0], v[:, 2]], 1), np.stack([me[:, 0], v[:, 1], v[:, 2]], 1)]
            out_r += [r, r]
        elif c == 2:
            m0, m1 = me[:, 0], me[:, 1]
            out_f.append(np.stack([m0, v[:, 1], m1], 1))
            out_r.append(r)
            short = (np.linalg.norm(P[v[:, 0]] - P[m1], axis=1) <= np.linalg.norm(P[m0] - P[v[:, 2]], axis=1))[:, None]
            out_f += [np.where(short, np.stack([v[:, 0], m0, m1], 1), np.stack([v[:, 0], m0, v[:, 2]], 1)),
                      np.where(short, np.stack([v[:, 0], m1, v[:, 2]], 1), np.stack([m0, m1, v[:, 2]], 1))]
            out_r += [r, r]
        else:
            m0, m1, m2 = me[:, 0], me[:, 1], me[:, 2]
            out_f += [np.stack([v[:, 0], m0, m2], 1), np.stack([m0, v[:, 1], m1], 1), np.stack([m2, m1, v[:, 2]], 1),
                      np.stack([m0, m1, m2], 1)]
            out_r += [r, r, r, r]
    return Mesh(P, np.vstack(out_f), np.concatenate(out_r), feat, vtype, size), len(idx)


def split_long(m: Mesh, ref: Reference, max_passes: int = 16) -> tuple[Mesh, int]:
    total = 0
    for _ in range(max_passes):
        m, k = _split_pass(m, ref)
        total += k
        if not k:
            break
    return m, total


class _Flat:
    """Projection that leaves points where they are (subdividing a surface without changing it)."""

    def surface(self, Q, qn, qr, k=8):
        return Q

    def feature(self, Q, k=8):
        return Q


def _reference(P: np.ndarray, F: np.ndarray, R: np.ndarray, feat: np.ndarray, h: float) -> Reference:
    """The original surface divided (in place, so unchanged in shape) until no edge is longer than
    ``h``: the nearest triangle centres to a point are then those of the triangles closest to it,
    also where the original has strips of long slivers (CAD exports)."""
    m = Mesh(P, F, R, feat, np.zeros(len(P), dtype=np.int8), np.full(len(P), 0.75 * h))
    m, _ = split_long(m, _Flat(), max_passes=64)  # type: ignore[arg-type]
    a, b = _unkey(m.feat)
    return Reference(m.P, m.F, m.R, np.stack([a, b], 1), cell=h)


# ---------------------------------------------------------------------------- 2. collapse


def _collapse_pass(m: Mesh, ref: Reference, rng: np.random.Generator) -> tuple[Mesh, int]:
    """Collapse an independent set of short edges."""
    P, F = m.P, m.F
    n = len(P)
    uniq, fe = _edges(F)
    E = len(uniq)
    ef, cnt = _edge_faces(fe, E)
    a, b = uniq[:, 0], uniq[:, 1]
    L = np.linalg.norm(P[a] - P[b], axis=1)
    tgt = np.minimum(m.size[a], m.size[b])
    isf = m.feature_mask(uniq)
    ta, tb = m.vtype[a], m.vtype[b]
    both_reg = (ta == REGULAR) & (tb == REGULAR) & ~isf & (cnt == 2)
    one_reg = ((ta == REGULAR) ^ (tb == REGULAR)) & ~isf & (cnt == 2)
    # along a feature line: both ends on features, not both corners
    # (two corners only when they are very close: a tiny fragment between two junctions)
    along = isf & (ta != REGULAR) & (tb != REGULAR) & (cnt <= 2)
    along &= ~((ta == CORNER) & (tb == CORNER)) | (L < 0.25 * tgt)
    ci = np.flatnonzero((L < 0.8 * tgt) & (both_reg | one_reg | along))
    if not len(ci):
        return m, 0
    ca, cb = a[ci], b[ci]
    # link condition: the endpoints' common neighbours are exactly the edge's opposite vertices
    vv_ptr, vv = _csr(np.r_[a, b], np.r_[b, a], n)
    rep, pos = _expand(vv_ptr, ca)
    w = vv[pos]
    allkeys = _keys(a, b)  # sorted: uniq rows are sorted lexicographically
    kq = _keys(cb[rep], w)
    ix = np.minimum(np.searchsorted(allkeys, kq), E - 1)
    hit = (w != cb[rep]) & (allkeys[ix] == kq)
    ok = np.bincount(rep[hit], minlength=len(ci)) == cnt[ci]
    ci, ca, cb = ci[ok], ca[ok], cb[ok]
    if not len(ci):
        return m, 0
    # independent set: no endpoint of a chosen collapse within one edge of another one's endpoints
    vf_ptr, vf = _csr(F.reshape(-1), np.repeat(np.arange(len(F)), 3), n)
    r1, p1 = _expand(vf_ptr, ca)
    r2, p2 = _expand(vf_ptr, cb)
    frep = np.r_[r1, r2]
    fid = np.r_[vf[p1], vf[p2]]
    prio = L[ci] / tgt[ci] + 0.5 * rng.random(len(ci))
    best = np.full(n, np.inf)
    np.minimum.at(best, ca, prio)
    np.minimum.at(best, cb, prio)
    ring_rep = np.repeat(frep, 3)
    lose = best[F[fid].reshape(-1)] < prio[ring_rep]
    win = np.bincount(ring_rep[lose], minlength=len(ci)) == 0
    sel_e = np.flatnonzero(win)
    ci, ca, cb = ci[sel_e], ca[sel_e], cb[sel_e]
    keepf = win[frep]
    frep = np.searchsorted(sel_e, frep[keepf])
    fid = fid[keepf]
    # where the collapsed vertex goes: the end that may not move stays (the non-regular one, a corner)
    swap = (one_reg[ci] & (m.vtype[ca] == REGULAR)) | (along[ci] & (m.vtype[cb] == CORNER))
    keep = np.where(swap, cb, ca)
    gone = np.where(swap, ca, cb)
    mid = both_reg[ci] | (along[ci] & (m.vtype[ca] == FEATURE) & (m.vtype[cb] == FEATURE))
    newp = P[keep].copy()
    newp[mid] = 0.5 * (P[ca[mid]] + P[cb[mid]])
    vn = _vertex_normals(P, F)
    pm = np.flatnonzero(mid)
    if len(pm):
        reg = both_reg[ci[pm]]
        q = pm[reg]
        if len(q):
            qn = vn[ca[q]] + vn[cb[q]]
            qn /= np.maximum(np.linalg.norm(qn, axis=1), 1e-300)[:, None]
            vr = _vertex_region(F, m.R, n)
            newp[q] = ref.surface(newp[q], qn, vr[ca[q]])
        if (~reg).any():
            newp[pm[~reg]] = ref.feature(newp[pm[~reg]])
    # faces around both ends after the collapse: none turns over; no edge at the moved vertex
    # longer than the split threshold (or than it was)
    fn, area = _face_normals(P, F)
    tri = F[fid]
    kp, gn, t = keep[frep], gone[frep], tgt[ci][frep]
    moved = (tri == kp[:, None]) | (tri == gn[:, None])
    alive = moved.sum(1) == 1  # faces on the collapsed edge disappear
    X0 = P[tri]
    X = np.where(moved[:, :, None], newp[frep][:, None, :], X0)
    nn = np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
    na = np.linalg.norm(nn, axis=1)
    # reference side: the face's own normal, or the vertex normal where the face is a sliver
    e0 = np.linalg.norm(X0 - np.roll(X0, -1, axis=1), axis=2)
    sliver = 2.0 * area[fid] < 0.02 * (e0 ** 2).sum(1)
    side = np.where(sliver[:, None], vn[kp] + vn[gn], fn[fid])
    side /= np.maximum(np.linalg.norm(side, axis=1), 1e-300)[:, None]
    good = (na > 1e-14 * t * t) & (np.einsum("ij,ij->i", nn, side) > 0.5 * na)
    el = np.linalg.norm(X - np.roll(X, -1, axis=1), axis=2)
    at_moved = moved | np.roll(moved, -1, axis=1)
    limit = np.maximum(4.0 / 3.0 * t[:, None], 1.05 * e0.max(axis=1, keepdims=True))
    good &= (np.where(at_moved, el - limit, -1.0) < 0).all(axis=1)
    good |= ~alive
    wi = np.flatnonzero(np.bincount(frep[~good], minlength=len(ci)) == 0)
    if not len(wi):
        return m, 0
    P = P.copy()
    P[keep[wi]] = newp[wi]
    size = m.size.copy()
    size[keep[wi]] = np.minimum(m.size[keep[wi]], m.size[gone[wi]])
    remap = np.arange(n)
    remap[gone[wi]] = keep[wi]
    F2 = remap[F]
    alive = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    return Mesh(P, F2[alive], m.R[alive], _remap_features(m.feat, remap), m.vtype, size), len(wi)


def collapse_short(m: Mesh, ref: Reference, rng: np.random.Generator, max_passes: int = 30) -> tuple[Mesh, int]:
    total = 0
    for _ in range(max_passes):
        m, k = _collapse_pass(m, ref, rng)
        total += k
        if k <= max(2, 0.005 * len(m.P)):
            break
    return _compact(m), total


# ---------------------------------------------------------------------------- 3. flip


def _flip_pass(m: Mesh, rng: np.random.Generator, max_dihedral_deg: float = 30.0) -> tuple[Mesh, int]:
    P, F = m.P, m.F
    n = len(P)
    uniq, fe = _edges(F)
    ef, cnt = _edge_faces(fe, len(uniq))
    isf = m.feature_mask(uniq)
    ci = np.flatnonzero((cnt == 2) & ~isf)
    if not len(ci):
        return m, 0
    f1, f2 = ef[ci, 0], ef[ci, 1]
    same = m.R[f1] == m.R[f2]
    ci, f1, f2 = ci[same], f1[same], f2[same]
    a, b = uniq[ci, 0], uniq[ci, 1]
    s1, s2 = F[f1], F[f2]
    m1 = (s1 != a[:, None]) & (s1 != b[:, None])
    m2 = (s2 != a[:, None]) & (s2 != b[:, None])
    good = (m1.sum(1) == 1) & (m2.sum(1) == 1)
    f1, f2, a, b, s1, s2, m1, m2 = f1[good], f2[good], a[good], b[good], s1[good], s2[good], m1[good], m2[good]
    c = s1[m1]
    d = s2[m2]
    val = np.bincount(np.r_[uniq[:, 0], uniq[:, 1]], minlength=n)
    target = np.full(n, 6)
    target[uniq[cnt == 1].reshape(-1)] = 4  # open boundary
    weight = (m.vtype != CORNER).astype(float)

    def dev(v, dv):
        return weight[v] * (val[v] + dv - target[v]) ** 2

    before = dev(a, 0) + dev(b, 0) + dev(c, 0) + dev(d, 0)
    after = dev(a, -1) + dev(b, -1) + dev(c, 1) + dev(d, 1)
    # slivers: also flip a pair that is far from Delaunay (angles opposite the edge > 200 deg)
    # unless that costs much valence
    def angle(at, p, q):
        u, v = P[p] - P[at], P[q] - P[at]
        return np.arctan2(np.linalg.norm(np.cross(u, v), axis=1), np.einsum("ij,ij->i", u, v))

    far = angle(c, a, b) + angle(d, a, b) > math.radians(200.0)
    want = (after < before) | (far & (after <= before + 2))
    want &= (val[a] > 3) & (val[b] > 3)
    # the new edge must not be longer than the split threshold (or than the old one)
    Lab = np.linalg.norm(P[a] - P[b], axis=1)
    Lcd = np.linalg.norm(P[c] - P[d], axis=1)
    want &= Lcd <= np.maximum(Lab, 4.0 / 3.0 * np.minimum(m.size[c], m.size[d]))
    fn, _ = _face_normals(P, F)
    want &= np.einsum("ij,ij->i", fn[f1], fn[f2]) > math.cos(math.radians(max_dihedral_deg))
    # face 1 runs a -> b or b -> a; the new faces keep that orientation and must not turn over
    k = np.argmax(s1 == a[:, None], axis=1)
    a_first = s1[np.arange(len(s1)), (k + 1) % 3] == b
    A = np.where(a_first, a, b)
    B = np.where(a_first, b, a)
    t1 = np.stack([A, d, c], 1)
    t2 = np.stack([d, B, c], 1)
    n1 = fn[f1] + fn[f2]
    for t in (t1, t2):
        x = P[t]
        nn = np.cross(x[:, 1] - x[:, 0], x[:, 2] - x[:, 0])
        want &= np.einsum("ij,ij->i", nn, n1) > 0.3 * np.linalg.norm(nn, axis=1) * np.linalg.norm(n1, axis=1)
    allkeys = _keys(uniq[:, 0], uniq[:, 1])
    kq = _keys(c, d)
    ix = np.minimum(np.searchsorted(allkeys, kq), len(allkeys) - 1)
    want &= (allkeys[ix] != kq) & (c != d)
    wi = np.flatnonzero(want)
    if not len(wi):
        return m, 0
    # independent: no two flips share a vertex (valences stay exact, faces change once)
    prio = rng.random(len(wi))
    best = np.full(n, 2.0)
    verts = np.stack([a[wi], b[wi], c[wi], d[wi]], 1)
    np.minimum.at(best, verts.reshape(-1), np.repeat(prio, 4))
    win = (best[verts] == prio[:, None]).all(1)
    sel = wi[win]
    F = F.copy()
    F[f1[sel]] = t1[sel]
    F[f2[sel]] = t2[sel]
    return Mesh(P, F, m.R, m.feat, m.vtype, m.size), len(sel)


def flip_valence(m: Mesh, rng: np.random.Generator, max_passes: int = 4) -> tuple[Mesh, int]:
    total = 0
    for _ in range(max_passes):
        m, k = _flip_pass(m, rng)
        total += k
        if not k:
            break
    return m, total


# ---------------------------------------------------------------------------- 4. relax


def relax(m: Mesh, ref: Reference, lam: float = 0.6) -> Mesh:
    P, F = m.P, m.F
    n = len(P)
    fn, area = _face_normals(P, F)
    cen = P[F].mean(axis=1)
    acc = np.zeros_like(P)
    w = np.zeros(n)
    for k in range(3):
        w += np.bincount(F[:, k], weights=area, minlength=n)
        acc += np.stack([np.bincount(F[:, k], weights=cen[:, d] * area, minlength=n) for d in range(3)], axis=1)
    target = acc / np.maximum(w, 1e-300)[:, None]
    vn = _vertex_normals(P, F)
    d = target - P
    d -= np.einsum("ij,ij->i", d, vn)[:, None] * vn
    out = P.copy()
    reg = np.flatnonzero(m.vtype == REGULAR)
    vr = _vertex_region(F, m.R, n)
    out[reg] = ref.surface(P[reg] + lam * d[reg], vn[reg], vr[reg])
    # feature vertices slide along their line towards the middle of their two neighbours on it
    fv = np.flatnonzero(m.vtype == FEATURE)
    if len(fv):
        fa, fb = _unkey(m.feat)
        s = np.zeros_like(P)
        c = np.bincount(fa, minlength=n) + np.bincount(fb, minlength=n)
        for dd in range(3):
            s[:, dd] = np.bincount(fa, weights=P[fb, dd], minlength=n) + np.bincount(fb, weights=P[fa, dd], minlength=n)
        ok = fv[c[fv] == 2]
        tgt = s[ok] / 2.0
        out[ok] = ref.feature(P[ok] + lam * (tgt - P[ok]))
    # undo moves that would turn a face over or squash it (a few rounds: undoing can expose others)
    for _ in range(4):
        nn = np.cross(out[F[:, 1]] - out[F[:, 0]], out[F[:, 2]] - out[F[:, 0]])
        flipped = np.einsum("ij,ij->i", nn, fn) <= 0.2 * np.linalg.norm(nn, axis=1)
        if not flipped.any():
            break
        back = np.unique(F[flipped].reshape(-1))
        out[back] = P[back]
    return Mesh(out, F, m.R, m.feat, m.vtype, m.size)


# ---------------------------------------------------------------------------- sizing


def update_sizes(m: Mesh, h_max: float, h_min: float, facet_angle_deg: float, max_faces: Optional[int], grade: float = 0.4) -> Mesh:
    """Size per vertex: h_max, smaller where neighbouring triangles meet at more than the facet
    angle (curved surface), not below h_min, varying by at most ``grade`` per unit length;
    scaled up when the triangles it implies exceed ``max_faces``."""
    P, F = m.P, m.F
    n = len(P)
    uniq, fe = _edges(F)
    ef, cnt = _edge_faces(fe, len(uniq))
    isf = m.feature_mask(uniq)
    fn, area = _face_normals(P, F)
    vn = _vertex_normals(P, F)
    s = np.full(n, h_max)
    # vertex normals average across a sharp edge, so only edges between regular vertices count
    two = np.flatnonzero((cnt == 2) & ~isf & (m.vtype[uniq[:, 0]] == REGULAR) & (m.vtype[uniq[:, 1]] == REGULAR))
    if len(two):
        # largest curvature at a vertex: the largest turn of the vertex normals per length along its
        # smooth edges (across a cylinder 1/r, along it 0)
        a, b = uniq[two, 0], uniq[two, 1]
        L = np.linalg.norm(P[a] - P[b], axis=1)
        turn = np.arccos(np.clip(np.einsum("ij,ij->i", vn[a], vn[b]), -1.0, 1.0)) / np.maximum(L, 1e-300)
        kappa = np.zeros(n)
        np.maximum.at(kappa, a, turn)
        np.maximum.at(kappa, b, turn)
        s = np.where(kappa > 0, math.radians(facet_angle_deg) / np.maximum(kappa, 1e-300), h_max)
    s = np.clip(s, h_min, h_max)
    a, b = uniq[:, 0], uniq[:, 1]
    L = np.linalg.norm(P[a] - P[b], axis=1)
    for _ in range(4):
        s0 = s.copy()
        np.minimum.at(s, a, s0[b] + grade * L)
        np.minimum.at(s, b, s0[a] + grade * L)
    if max_faces:
        # a remeshed surface has about FILL times the triangles of an equilateral tiling at the target size
        est = FILL * float((area / (EQ_AREA * s[F].mean(axis=1) ** 2)).sum())
        if est > max_faces:
            s = s * math.sqrt(est / max_faces)
    return Mesh(P, F, m.R, m.feat, m.vtype, s)


# ---------------------------------------------------------------------------- driver


@dataclass
class RemeshResult:
    points: np.ndarray
    faces: np.ndarray
    regions: np.ndarray
    feature_pairs: np.ndarray  # feature edges of the result (vertex pairs)
    info: dict = field(default_factory=dict)


def remesh(points: np.ndarray, faces: np.ndarray, regions: np.ndarray, h: float, h_min: Optional[float] = None,
           max_faces: Optional[int] = None, feature_angle: float = 40.0, facet_angle: float = 15.0, iterations: int = 6,
           seed: int = 0) -> RemeshResult:
    """Remesh to edges of about ``h`` (down to ``h_min`` on tight curves), keeping features."""
    P0 = np.asarray(points, dtype=float)
    F0 = np.asarray(faces, dtype=np.int64)
    R0 = np.asarray(regions)
    h_min = min(float(h_min) if h_min else h, h)
    if len(P0) >= int(KEYBASE) // 8:
        raise ValueError("surface too large to remesh")
    pairs = detect_features(P0, F0, R0, feature_angle, min_length=max(2.0 * h_min, h))
    feat = np.unique(_keys(pairs[:, 0], pairs[:, 1])) if len(pairs) else np.zeros(0, dtype=np.int64)
    ref = _reference(P0, F0, R0, feat, h)
    # small closed pieces (fewer than 40 triangles: bolts, tabs) are kept as they are
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n0 = len(P0)
    g = coo_matrix((np.ones(3 * len(F0)), (np.repeat(np.arange(len(F0)), 3), F0.reshape(-1))), shape=(len(F0), n0)).tocsr()
    _, lab = connected_components(g @ g.T, directed=False)
    small = np.bincount(lab)[lab] < 40
    fixed = np.zeros(n0, dtype=bool)
    fixed[F0[small].reshape(-1)] = True
    m = Mesh(P0.copy(), F0.copy(), R0.copy(), feat, _vertex_types(n0, feat, fixed), np.full(n0, float(h)))
    rng = np.random.default_rng(seed)
    stats = {"splits": 0, "collapses": 0, "flips": 0}
    for it in range(iterations):
        # collapse first: a fine or sliver-strewn input shrinks before long edges are divided
        m, k = collapse_short(m, ref, rng)
        stats["collapses"] += k
        m, k = split_long(m, ref)
        stats["splits"] += k
        m, k = collapse_short(m, ref, rng)
        stats["collapses"] += k
        m, k = flip_valence(m, rng)
        stats["flips"] += k
        m = relax(m, ref)
        if 1 <= it < iterations - 2 and (h_min < h or max_faces):  # once the mesh is even enough to measure curvature
            m = update_sizes(m, h, h_min, facet_angle, max_faces)
    # relaxing can stretch a few edges past the split threshold: divide those, and flip once more
    m, k = split_long(m, ref)
    stats["splits"] += k
    m, k = flip_valence(m, rng)
    stats["flips"] += k
    m = _compact(m)
    q = quality(m.P, m.F)
    fa, fb = _unkey(m.feat)
    t = m.P[m.F]
    e = np.linalg.norm(t - np.roll(t, -1, axis=1), axis=2)
    info = {**stats, "faces_in": int(len(F0)), "faces_out": int(len(m.F)), "h": float(h), "h_min": float(h_min),
            "feature_edges": int(len(feat)), "quality_median": float(np.median(q)) if len(q) else None,
            "quality_min": float(q.min()) if len(q) else None, "poor": int((q < 0.2).sum()),
            "edge_min": float(e.min()) if len(e) else None, "edge_max": float(e.max()) if len(e) else None,
            "area_in": float(_face_normals(P0, F0)[1].sum()), "area_out": float(_face_normals(m.P, m.F)[1].sum())}
    return RemeshResult(m.P, m.F, m.R, np.stack([fa, fb], 1), info)
