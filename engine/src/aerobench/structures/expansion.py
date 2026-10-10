"""Will CalculiX be able to expand the shells? (checked before solving)

CalculiX solves shell elements as 3D elements: every S6 triangle is expanded along nodal
normals into a 15-node wedge spanning the thickness. Where a thin triangle sits on a tight
curve (a leading edge rounded more tightly than the laminate is thick, a sliver of a CAD
tessellation along it) the expanded wedge folds over and CalculiX stops with "nonpositive
jacobian". This module repeats CalculiX's construction so the problem is found — and
avoided — before the solver runs:

* nodal normals as ``gen3dnor`` builds them: at each node the elements are taken in element
  order; the first untreated element's normal is the reference and every element whose
  normal is within 20° of it shares the mean of their normals (others get their own normal);
* the Jacobian of the expanded wedge on a dense grid over the triangle and the thickness
  (denser than the solver's integration points, so the check errs on the safe side).

``choose_offsets`` keeps each section on the inner side of the surface when it fits
(OFFSET = 0.5) and centres it on the surface where it does not (OFFSET = 0, half the
thickness each way, which fits curves twice as tight).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

COS20 = 0.9396926  # gen3dnor: normals within 20 degrees share one nodal normal
MARGIN = 0.02  # smallest acceptable ratio of the expanded to the flat Jacobian


def ccx_normals(conn: np.ndarray, face_normals: np.ndarray) -> np.ndarray:
    """(M, k, 3) normal per element and local node, grouped like CalculiX's gen3dnor."""
    M, k = conn.shape
    el = np.repeat(np.arange(M), k)
    nd = conn.reshape(-1)
    order = np.lexsort((el, nd))  # by node, then element number
    nd_s, el_s = nd[order], el[order]
    starts = np.flatnonzero(np.r_[True, nd_s[1:] != nd_s[:-1]])
    counts = np.diff(np.r_[starts, len(nd_s)])
    grp = np.repeat(np.arange(len(starts)), counts)
    n_s = face_normals[el_s]
    ref = n_s[starts]
    split = np.zeros(len(starts), dtype=bool)
    np.logical_or.at(split, grp, np.einsum("ij,ij->i", n_s, ref[grp]) <= COS20)
    acc = np.zeros((len(starts), 3))
    np.add.at(acc, grp, n_s)
    acc /= np.maximum(np.linalg.norm(acc, axis=1), 1e-300)[:, None]
    out = acc[grp]
    for g in np.flatnonzero(split):  # nodes on sharp edges: several normals
        idx = np.arange(starts[g], starts[g] + counts[g])
        n = n_s[idx]
        todo = np.ones(len(idx), dtype=bool)
        while todo.any():
            j = int(np.flatnonzero(todo)[0])
            sel = todo & (n @ n[j] > COS20)
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


@dataclass
class OffsetChoice:
    offsets: list[float]  # per section: 0.5 (inside the surface) or 0.0 (centred on it)
    failing: dict[int, np.ndarray] = field(default_factory=dict)  # section -> elements that fold even when centred
    centred: list[int] = field(default_factory=list)  # sections moved to the centre


def choose_offsets(nodes: np.ndarray, conn: np.ndarray, face_normals: np.ndarray, section_of: np.ndarray,
                   thickness: list[float], preferred: float = 0.5) -> OffsetChoice:
    normals = ccx_normals(conn, face_normals)
    t = np.asarray(thickness, dtype=float)[section_of]
    out = OffsetChoice(offsets=[preferred] * len(thickness))
    for k in range(len(thickness)):
        sel = np.flatnonzero(section_of == k)
        if not len(sel):
            continue
        for off in ([preferred, 0.0] if preferred != 0.0 else [0.0]):
            # OFFSET = o: the mid-surface is o * t below the nodes (against the normal)
            lo, hi = -(off + 0.5) * t[sel], -(off - 0.5) * t[sel]
            r = jacobian_ratio(nodes, conn[sel], normals[sel], lo, hi)
            bad = sel[r <= MARGIN]
            if not len(bad):
                out.offsets[k] = off
                if off != preferred:
                    out.centred.append(k)
                out.failing.pop(k, None)
                break
            out.failing[k] = bad
    return out
