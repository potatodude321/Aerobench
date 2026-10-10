"""Aerodynamic wall loads of a CFD run applied to the structural mesh.

Per boundary face of the flow mesh (triangulated) the force is

    F = rho * (p - p_ref) * S  -  rho * tau_w * |S|

with OpenFOAM's kinematic pressure ``p`` and ``wallShearStress`` (both per unit density) and
the face area vector ``S`` pointing out of the fluid, into the part — the same convention as
OpenFOAM's ``forces`` function object, which the total is checked against.

The flow faces and the structural triangles cover the same surface but have unrelated sizes
(the structural mesh may be finer or coarser than the flow mesh at the wall). The wall
traction F/|S| of the flow faces is therefore sampled over every structural triangle (the
centroids of an n x n subdivision, n chosen so that each flow face is sampled at least about
once) and each sample's force is shared between the six nodes of the quadratic shell with
its shape functions — consistent loads, which keep force and moment of every sample exactly.
A sample takes the traction of the nearest flow face on the same side of the part (the face
normal must agree with the shell's). What remains of the difference to the flow solution's
total (sampling error, usually a fraction of a percent) is spread as a uniform traction over
the part, so the applied total equals the integrated wall force exactly; its size is
reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .mesh import StructMesh


@dataclass
class MappedLoads:
    nodal: np.ndarray  # (N, 3) forces on all S6 nodes, corners first (N)
    total: np.ndarray  # (3,) sum of the face forces (N)
    pressure_total: np.ndarray
    viscous_total: np.ndarray
    tri_traction: np.ndarray  # (M, 3) force per structural triangle / its area (Pa), for display
    max_distance: float  # how much further than a face size a sample was from the nearest flow face (m): surface mismatch
    faces: int
    notes: list[str] = field(default_factory=list)
    correction: float = 0.0  # largest share of a part's force spread uniformly after sampling


def face_forces(data: dict, patch: str, rho: float, p_ref: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(centres, total force, pressure part, viscous part) per CFD face of a patch."""
    c = np.asarray(data[f"{patch}/centre"], dtype=float)
    S = np.asarray(data[f"{patch}/area"], dtype=float)
    p = np.asarray(data[f"{patch}/p"], dtype=float)
    w = np.asarray(data[f"{patch}/wss"], dtype=float)
    fp = rho * (p - p_ref)[:, None] * S
    fv = -rho * w * np.linalg.norm(S, axis=1)[:, None]
    return c, fp + fv, fp, fv


_SUBDIV: dict[int, np.ndarray] = {}


def subdivision_points(n: int) -> np.ndarray:
    """Barycentric coordinates (L1, L2, L3) of the centroids of the n*n triangles of a uniform
    subdivision of a triangle; equal weights 1/n^2."""
    if n not in _SUBDIV:
        uv = []
        for i in range(n):
            for j in range(n - i):
                uv.append(((i + 1 / 3) / n, (j + 1 / 3) / n))
                if i + j <= n - 2:
                    uv.append(((i + 2 / 3) / n, (j + 2 / 3) / n))
        a = np.array(uv)
        _SUBDIV[n] = np.stack([1.0 - a.sum(1), a[:, 0], a[:, 1]], axis=1)
    return _SUBDIV[n]


def s6_shape(L: np.ndarray) -> np.ndarray:
    """Shape functions of the 6-node triangle (corners 1-3, then mid-edges 1-2, 2-3, 3-1)."""
    L1, L2, L3 = L[:, 0], L[:, 1], L[:, 2]
    return np.stack([L1 * (2 * L1 - 1), L2 * (2 * L2 - 1), L3 * (2 * L3 - 1), 4 * L1 * L2, 4 * L2 * L3, 4 * L3 * L1], axis=1)


def _nearest_same_side(tree, centres_n: np.ndarray, q: np.ndarray, qn: np.ndarray, closed: bool, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest flow face to each query point whose normal agrees with the shell
    normal ``qn`` (flow face normals point into the part, shell normals out of it on closed
    parts); falls back to the nearest face."""
    kk = min(k, tree.n)
    d, cand = tree.query(q, k=kk)
    d, cand = np.asarray(d).reshape(len(q), kk), np.asarray(cand).reshape(len(q), kk)
    dots = np.einsum("nkd,nd->nk", centres_n[cand], qn)
    ok = dots < -0.2 if closed else np.abs(dots) > 0.2
    first = np.where(ok.any(axis=1), np.argmax(ok, axis=1), 0)
    r = np.arange(len(q))
    return cand[r, first], d[r, first]


def map_loads(mesh: StructMesh, data: dict, rho: float, p_ref: float, k: int = 8, max_subdivision: int = 8) -> MappedLoads:
    """Consistent nodal loads on the S6 nodes (corners first, then mid-edge nodes, as
    ``mesh.s6()``) from the face forces of every analysed part's patch (patch name = part id)."""
    from scipy.spatial import cKDTree

    nodes, conn = mesh.s6()
    nodal = np.zeros((len(nodes), 3))
    tri_force = np.zeros((mesh.n_tris, 3))
    total, ptot, vtot = np.zeros(3), np.zeros(3), np.zeros(3)
    max_gap = 0.0
    nfaces = 0
    worst_corr = 0.0
    notes: list[str] = []
    area = mesh.areas()
    normal = mesh.normals()
    corners = mesh.points[mesh.tris]  # (M, 3, 3)
    closed = set(mesh.closed_parts)
    for pi, pid in enumerate(mesh.part_ids):
        sel = np.flatnonzero(mesh.part == pi)
        if len(sel) == 0:
            continue
        if f"{pid}/centre" not in data or len(data[f"{pid}/centre"]) == 0:
            notes.append(f"No flow loads were found for part '{pid}'.")
            continue
        c, f, fp, fv = face_forces(data, pid, rho, p_ref)
        S = np.asarray(data[f"{pid}/area"], dtype=float)
        a_face = np.linalg.norm(S, axis=1)
        good = a_face > 0
        nfaces += len(c)
        f_part = f.sum(0)
        total += f_part
        ptot += fp.sum(0)
        vtot += fv.sum(0)
        c, f, S, a_face = c[good], f[good], S[good], a_face[good]
        tau = f / a_face[:, None]  # traction (Pa)
        fn = S / a_face[:, None]
        tree = cKDTree(c)
        a_ref = float(np.median(a_face))
        n_sub = np.clip(np.ceil(np.sqrt(area[sel] / max(a_ref, 1e-300))), 2, max_subdivision).astype(np.int64)
        mapped = np.zeros(3)
        for n in np.unique(n_sub):
            els = sel[n_sub == n]
            B = subdivision_points(int(n))  # (s, 3)
            N = s6_shape(B)  # (s, 6)
            q = np.einsum("sk,mkd->msd", B, corners[els]).reshape(-1, 3)
            qn = np.repeat(normal[els], len(B), axis=0)
            idx, dist = _nearest_same_side(tree, fn, q, qn, pid in closed, k)
            t = tau[idx].reshape(len(els), len(B), 3)
            w = (area[els] / len(B))[:, None]  # (m, 1)
            for a in range(6):
                np.add.at(nodal, conn[els, a], w * np.einsum("s,msd->md", N[:, a], t))
            fe = w * t.sum(axis=1)
            tri_force[els] += fe
            mapped += fe.sum(0)
            # a sample further from any flow face centre than the face size means the surfaces differ
            h = np.sqrt(a_face[idx])
            max_gap = max(max_gap, float(np.max(dist - 1.5 * h)) if len(dist) else 0.0)
        # spread the remaining difference uniformly (mid-edge nodes carry a uniform load on S6)
        corr = f_part - mapped
        if np.linalg.norm(f_part) > 0:
            worst_corr = max(worst_corr, float(np.linalg.norm(corr) / np.linalg.norm(f_part)))
        share = (area[sel] / area[sel].sum())[:, None] * corr[None, :]
        for a in range(3, 6):
            np.add.at(nodal, conn[sel, a], share / 3.0)
        tri_force[sel] += share
    traction = tri_force / np.maximum(area, 1e-300)[:, None]
    if worst_corr > 0.03:
        notes.append(f"Sampling the flow loads on the structural mesh missed {worst_corr * 100:.1f} % of the force of a part; "
                     "the difference was spread evenly over it. Check that the part's surface is the one simulated.")
    return MappedLoads(nodal=nodal, total=total, pressure_total=ptot, viscous_total=vtot, tri_traction=traction,
                       max_distance=max(0.0, max_gap), faces=nfaces, notes=notes, correction=worst_corr)
