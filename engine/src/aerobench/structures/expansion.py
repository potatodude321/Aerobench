"""Will CalculiX be able to expand the shells? (planned before solving)

CalculiX solves shell elements as 3D elements: every S6 triangle is expanded along nodal
normals into a 15-node wedge spanning the thickness. The nodal normals come from the facets
(``gen3dnor``): at each node the elements are taken in element order; the first untreated one
is the reference, and every element within 20 degrees of it that has the same thickness and
offset shares the mean of their normals; elements left over get a normal of their own, and the
node becomes a knot joining the expansions rigidly. Where a facet sits next to a kink or on a
curve tighter than the laminate is thick, the shared normal crosses the wedge and it folds over
("nonpositive jacobian").

This module repeats that construction (``ccx_normals``) and checks the Jacobian of every
expanded wedge on a dense grid over the triangle and the thickness (``jacobian_ratio``; denser
than the solver's integration points, so it errs on the safe side). ``plan_expansion`` keeps
each section on the inner side of the surface (OFFSET = 0.5) and *isolates* an element whose
wedge would fold: it gets the section with an offset 1 % of the thickness different, so by the
rule above it no longer shares normals with its neighbours — it is expanded along its own facet
normal (a straight prism, which cannot fold) and joined to them by knots, locally a little
stiffer. Isolating an element changes its neighbours' shared normals, so the check repeats
until nothing folds. Only when a large part of a section folds is it centred on the surface
instead (half the thickness each way fits curves twice as tight).

(CalculiX also reads normals given with ``*NORMAL``, but version 2.21 does not expand composite
sections along them correctly, so the plan works with the solver's own normals.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

COS20 = 0.9396926  # gen3dnor: normals within 20 degrees share one nodal normal
MARGIN = 0.02  # smallest acceptable ratio of the expanded to the flat Jacobian


def ccx_normals(conn: np.ndarray, face_normals: np.ndarray, key: np.ndarray | None = None) -> np.ndarray:
    """(M, k, 3) normal per element and local node, grouped like CalculiX's gen3dnor. ``key``
    (M,): elements with different keys (thickness and offset) never share a normal."""
    M, k = conn.shape
    key = np.zeros(M, dtype=np.int64) if key is None else np.asarray(key, dtype=np.int64)
    el = np.repeat(np.arange(M), k)
    nd = conn.reshape(-1)
    order = np.lexsort((el, nd))  # by node, then element number
    nd_s, el_s = nd[order], el[order]
    starts = np.flatnonzero(np.r_[True, nd_s[1:] != nd_s[:-1]])
    counts = np.diff(np.r_[starts, len(nd_s)])
    grp = np.repeat(np.arange(len(starts)), counts)
    n_s = face_normals[el_s]
    k_s = key[el_s]
    ref = n_s[starts]
    split = np.zeros(len(starts), dtype=bool)
    np.logical_or.at(split, grp, (np.einsum("ij,ij->i", n_s, ref[grp]) <= COS20) | (k_s != k_s[starts][grp]))
    acc = np.zeros((len(starts), 3))
    np.add.at(acc, grp, n_s)
    acc /= np.maximum(np.linalg.norm(acc, axis=1), 1e-300)[:, None]
    out = acc[grp]
    for g in np.flatnonzero(split):  # nodes on sharp edges or between sections: several normals
        idx = np.arange(starts[g], starts[g] + counts[g])
        n = n_s[idx]
        kk = k_s[idx]
        todo = np.ones(len(idx), dtype=bool)
        while todo.any():
            j = int(np.flatnonzero(todo)[0])
            sel = todo & (n @ n[j] > COS20) & (kk == kk[j])
            sel[j] = True
            v = n[sel].sum(0)
            out[idx[sel]] = v / max(float(np.linalg.norm(v)), 1e-300)
            todo &= ~sel
    flat = np.zeros((M * k, 3))
    flat[order] = out
    return flat.reshape(M, k, 3)


def _s6_at(L1: float, L2: float, L3: float):
    N = np.array([L1 * (2 * L1 - 1), L2 * (2 * L2 - 1), L3 * (2 * L3 - 1), 4 * L1 * L2, 4 * L2 * L3, 4 * L3 * L1])
    dN2 = np.array([-(4 * L1 - 1), 4 * L2 - 1, 0.0, 4 * (L1 - L2), 4 * L3, -4 * L3])
    dN3 = np.array([-(4 * L1 - 1), 0.0, 4 * L3 - 1, -4 * L2, 4 * L2, 4 * (L1 - L3)])
    return N, dN2, dN3


def jacobian_ratio(nodes: np.ndarray, conn: np.ndarray, normals: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                   n: int = 8, nz: int = 5) -> np.ndarray:
    """Smallest ratio of the expanded wedge's Jacobian to the flat triangle's, per element.
    ``lo``/``hi``: (M,) the shell's extent along the normal from the nodes (m)."""
    XC = nodes[conn]
    worst = np.full(len(conn), np.inf)
    for i in range(n + 1):
        for j in range(n + 1 - i):
            L2, L3 = i / n, j / n
            N, dN2, dN3 = _s6_at(1.0 - L2 - L3, L2, L3)
            a0, b0 = np.einsum("k,mkd->md", dN2, XC), np.einsum("k,mkd->md", dN3, XC)
            an, bn = np.einsum("k,mkd->md", dN2, normals), np.einsum("k,mkd->md", dN3, normals)
            c = np.einsum("k,mkd->md", N, normals)
            ref = np.maximum(np.linalg.norm(np.cross(a0, b0), axis=1), 1e-300)
            for f in np.linspace(0.0, 1.0, nz):
                s = (lo + f * (hi - lo))[:, None]
                det = np.einsum("md,md->m", np.cross(a0 + s * an, b0 + s * bn), c)
                worst = np.minimum(worst, det / ref)
    return worst


ISOLATE = 0.01  # offset change (share of the thickness) that gives an element normals of its own


@dataclass
class ExpansionPlan:
    offsets: list[float]  # per section: 0.5 (inside the surface) or 0.0 (centred on it)
    isolated: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # elements expanded along their own facet
    colour: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # per element: -1, or the isolated group
    isolated_area_fraction: float = 0.0  # their share of the area
    centred: list[int] = field(default_factory=list)  # sections moved to the centre
    still_folding: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # (degenerate) elements that fold anyway

    def element_offsets(self, section_of: np.ndarray) -> np.ndarray:
        return np.asarray(self.offsets, dtype=float)[section_of] - ISOLATE * (self.colour + 1)


def _colour(conn: np.ndarray, iso: np.ndarray, colour: np.ndarray) -> None:
    """Give newly isolated elements a group such that no two isolated elements sharing a node are
    in the same group (two neighbours in one group would share their normals again)."""
    node_cols: dict[int, set[int]] = {}
    for e in np.flatnonzero(colour >= 0):
        for v in conn[e]:
            node_cols.setdefault(int(v), set()).add(int(colour[e]))
    for e in np.flatnonzero(iso & (colour < 0)):
        used: set[int] = set()
        for v in conn[e]:
            used |= node_cols.get(int(v), set())
        c = 0
        while c in used:
            c += 1
        colour[e] = c
        for v in conn[e]:
            node_cols.setdefault(int(v), set()).add(c)


def plan_expansion(nodes: np.ndarray, conn: np.ndarray, section_of: np.ndarray, thickness: list[float], preferred: float = 0.5,
                   centre_above: float = 0.1, max_rounds: int = 8) -> ExpansionPlan:
    """Offsets per section and the elements to isolate so that every wedge expands without folding."""
    M = len(conn)
    t = nodes[conn[:, :3]]
    fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    area = 0.5 * np.linalg.norm(fn, axis=1)
    fn = fn / np.maximum(2.0 * area, 1e-300)[:, None]
    th = np.asarray(thickness, dtype=float)[section_of]
    plan = ExpansionPlan(offsets=[preferred] * len(thickness), colour=-np.ones(M, dtype=np.int64))
    check = np.ones(M, dtype=bool)
    sec_area = np.bincount(section_of, weights=area, minlength=len(thickness))
    for _ in range(max_rounds):
        off = plan.element_offsets(section_of)
        # elements with the same thickness and offset share normals (the rule CalculiX applies)
        _, key = np.unique(np.round(np.stack([th, off], axis=1), 12), axis=0, return_inverse=True)
        normals = ccx_normals(conn, fn, key.reshape(-1))
        sel = np.flatnonzero(check)
        lo, hi = -(off[sel] + 0.5) * th[sel], -(off[sel] - 0.5) * th[sel]
        bad = sel[jacobian_ratio(nodes, conn[sel], normals[sel], lo, hi) <= MARGIN]
        if not len(bad):
            break
        # a section that folds over a large part of its area is centred on the surface instead
        frac = np.bincount(section_of[bad], weights=area[bad], minlength=len(thickness)) / np.maximum(sec_area, 1e-300)
        move = [k for k in range(len(thickness)) if frac[k] > centre_above and plan.offsets[k] != 0.0 and k not in plan.centred]
        if move:
            for k in move:
                plan.offsets[k] = 0.0
                plan.centred.append(k)
            plan.colour[:] = -1
            check[:] = True
            continue
        new = bad[plan.colour[bad] < 0]
        if not len(new):  # isolated elements that still fold (degenerate facets): nothing more to do
            plan.still_folding = bad
            break
        iso = np.zeros(M, dtype=bool)
        iso[new] = True
        _colour(conn, iso, plan.colour)
        # recheck the elements whose shared normals change: those touching the newly isolated ones
        touched = np.zeros(len(nodes), dtype=bool)
        touched[conn[new].reshape(-1)] = True
        check = touched[conn].any(axis=1)
    else:
        plan.still_folding = bad
    plan.isolated = np.flatnonzero(plan.colour >= 0)
    plan.isolated_area_fraction = float(area[plan.isolated].sum() / max(area.sum(), 1e-300))
    return plan
