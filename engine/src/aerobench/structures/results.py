"""Reading the CalculiX results and judging them.

* metals and plastics: von Mises stress at every integration point against the yield
  strength (reserve factor RF = yield / von Mises; ultimate strength reported too);
* composite plies: in-plane ply stresses (CalculiX reports them in the ply axes: 1 = fibre)
  checked with the Tsai-Wu criterion (interaction F12 = -1/2 sqrt(F11 F22)) and the maximum
  stress criterion, which also names the failure mode (fibre tension/compression, matrix
  tension/compression, in-plane shear). The reserve factor is the factor on all loads at
  which the criterion reaches 1; the smaller of the two criteria governs.

Stresses are at the integration points (inside the elements), not extrapolated to edges or
supports: peaks at sharp corners and right at supports are mesh dependent, as in any shell
model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..domain.structure import Material, MaterialKind

_BLOCK = re.compile(r"^\s*(displacements|stresses|total force)\b.*?for set (\S+)", re.M)


def parse_dat(path: Path) -> dict[str, Any]:
    """Blocks of a CalculiX .dat file (last increment of each kind)."""
    text = path.read_text(errors="replace")
    out: dict[str, Any] = {}
    heads = list(_BLOCK.finditer(text))
    for k, m in enumerate(heads):
        start = text.index("\n", m.end()) + 1
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        body = text[start:end]
        rows = [ln.split() for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith(("S T E P", "INCREMENT"))]
        kind, setname = m.group(1), m.group(2)
        if kind == "displacements":
            a = np.array([[float(x) for x in r[:4]] for r in rows if len(r) >= 4])
            out[f"U:{setname}"] = a
        elif kind == "stresses":
            a = np.array([[float(x) for x in r[:8]] for r in rows if len(r) >= 8])
            out[f"S:{setname}"] = a
        elif kind == "total force":
            vals = [float(x) for r in rows for x in r[:3]]
            out[f"RF:{setname}"] = np.array(vals[:3])
    return out


def von_mises(s: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, sxz, syz = (s[:, i] for i in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + sxz ** 2 + syz ** 2))


def tsai_wu_rf(m: Material, s1: np.ndarray, s2: np.ndarray, t12: np.ndarray) -> np.ndarray:
    Xt, Xc, Yt, Yc, S = (float(getattr(m, k)) for k in ("Xt", "Xc", "Yt", "Yc", "S12"))
    F1, F2 = 1 / Xt - 1 / Xc, 1 / Yt - 1 / Yc
    F11, F22, F66 = 1 / (Xt * Xc), 1 / (Yt * Yc), 1 / S ** 2
    F12 = -0.5 * np.sqrt(F11 * F22)
    a = F11 * s1 ** 2 + F22 * s2 ** 2 + F66 * t12 ** 2 + 2 * F12 * s1 * s2
    b = F1 * s1 + F2 * s2
    with np.errstate(divide="ignore", invalid="ignore"):
        # a R^2 + b R - 1 = 0, positive root (a > 0 for any non-zero stress state)
        r = np.where(a > 1e-300, (-b + np.sqrt(b * b + 4 * a)) / (2 * a), np.where(b > 1e-300, 1 / b, np.inf))
    return r


MODES = ["fibre tension", "fibre compression", "matrix tension", "matrix compression", "in-plane shear"]


def max_stress_rf(m: Material, s1: np.ndarray, s2: np.ndarray, t12: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    with np.errstate(divide="ignore"):
        r = np.stack([np.where(s1 > 0, m.Xt / np.abs(s1), np.inf), np.where(s1 < 0, m.Xc / np.abs(s1), np.inf),  # type: ignore[operator]
                      np.where(s2 > 0, m.Yt / np.abs(s2), np.inf), np.where(s2 < 0, m.Yc / np.abs(s2), np.inf),  # type: ignore[operator]
                      m.S12 / np.maximum(np.abs(t12), 1e-300)], axis=1)  # type: ignore[operator]
    return r.min(axis=1), r.argmin(axis=1)


@dataclass
class Assessment:
    element_rf: np.ndarray  # (M,) smallest reserve factor in the element
    element_vm: np.ndarray  # (M,) largest von Mises stress (Pa), NaN for composite-only elements
    element_layer: np.ndarray  # (M,) critical ply (1 = outermost), 0 for sheets
    element_mode: np.ndarray  # (M,) index into MODES (-1: yield)
    summary: dict[str, Any] = field(default_factory=dict)


def assess(stresses: np.ndarray, n_elem: int, element_section: list[int], element_layers: list[list[str]],
           section_kinds: list[str], materials: dict[str, Material]) -> Assessment:
    """Reserve factor per element from the integration-point stresses."""
    el = stresses[:, 0].astype(np.int64) - 1
    ip = stresses[:, 1].astype(np.int64) - 1
    s = stresses[:, 2:8]
    sec = np.asarray(element_section)[el]
    rf = np.full(len(s), np.inf)
    mode = np.full(len(s), -1)
    layer = np.zeros(len(s), dtype=np.int64)
    vm = von_mises(s)
    for k, kind in enumerate(section_kinds):
        sel = np.flatnonzero(sec == k)
        if not len(sel):
            continue
        mats = element_layers[k]
        if kind == "sheet":
            m = materials[mats[0]]
            rf[sel] = m.yield_strength / np.maximum(vm[sel], 1e-300)  # type: ignore[operator]
            continue
        n_layers = len(mats)
        lay = np.minimum(ip[sel] // 6, n_layers - 1)  # S6 composite: 6 integration points per layer
        for li in range(n_layers):
            idx = sel[lay == li]
            if not len(idx):
                continue
            m = materials[mats[li]]
            layer[idx] = n_layers - li  # 1 = outermost ply
            if m.kind == MaterialKind.ISOTROPIC:
                rf[idx] = m.yield_strength / np.maximum(vm[idx], 1e-300)  # type: ignore[operator]
                continue
            s1, s2, t12 = s[idx, 0], s[idx, 1], s[idx, 3]
            r_tw = tsai_wu_rf(m, s1, s2, t12)
            r_ms, md = max_stress_rf(m, s1, s2, t12)
            rf[idx] = np.minimum(r_tw, r_ms)
            mode[idx] = md
    e_rf = np.full(n_elem, np.inf)
    np.minimum.at(e_rf, el, rf)
    e_vm = np.zeros(n_elem)
    np.maximum.at(e_vm, el, vm)
    # attributes of the governing point of each element
    order = np.lexsort((rf, el))
    first = np.ones(len(order), dtype=bool)
    first[1:] = el[order][1:] != el[order][:-1]
    gov = order[first]
    e_layer = np.zeros(n_elem, dtype=np.int64)
    e_mode = np.full(n_elem, -1)
    e_layer[el[gov]] = layer[gov]
    e_mode[el[gov]] = mode[gov]
    return Assessment(element_rf=e_rf, element_vm=e_vm, element_layer=e_layer, element_mode=e_mode)


def summarise(asm: Assessment, U: np.ndarray, centroids: np.ndarray, required: float, mass: float, reactions: Optional[np.ndarray],
              applied: np.ndarray, weight: np.ndarray, element_section: list[int], section_names: list[str],
              held: Optional[np.ndarray] = None, reactions_exact: bool = True) -> dict[str, Any]:
    """``held``: the part of applied + weight that sits on supported nodes (not in the deck);
    ``reactions_exact``: False when clamped edges pass reactions through generated constraints."""
    finite = np.isfinite(asm.element_rf)
    if not finite.any():
        crit = None
        min_rf = None
    else:
        crit = int(np.argmin(np.where(finite, asm.element_rf, np.inf)))
        min_rf = float(asm.element_rf[crit])
    disp = np.linalg.norm(U, axis=1) if len(U) else np.zeros(1)
    imax = int(np.argmax(disp)) if len(disp) else 0
    held = np.zeros(3) if held is None else np.asarray(held, dtype=float)
    load = applied + weight
    carried = load - held  # what the structure carries to its supports
    balance = None
    if reactions is not None and reactions_exact and np.linalg.norm(carried) > 0:
        balance = float(np.linalg.norm(reactions + carried) / np.linalg.norm(carried))
    out: dict[str, Any] = {
        "min_reserve": min_rf, "required_reserve": required, "passed": (min_rf is not None and min_rf >= required),
        "max_displacement": float(disp[imax]) if len(disp) else 0.0, "max_displacement_node": imax,
        "max_displacement_vector": U[imax].tolist() if len(U) else [0, 0, 0],
        "max_von_mises": float(np.nanmax(asm.element_vm)) if len(asm.element_vm) else None,
        "mass": mass, "applied_force": applied.tolist(), "weight": weight.tolist(),
        "reaction_total": reactions.tolist() if reactions is not None else None, "equilibrium_error": balance,
        "held_by_supports": held.tolist(),
        "held_fraction": float(np.linalg.norm(held) / np.linalg.norm(load)) if np.linalg.norm(load) > 0 else 0.0,
    }
    if crit is not None:
        sec = element_section[crit]
        out["critical"] = {"element": crit, "location": centroids[crit].tolist(), "section": section_names[sec],
                           "ply": int(asm.element_layer[crit]) or None,
                           "mode": (MODES[asm.element_mode[crit]] if asm.element_mode[crit] >= 0 else "yield")}
    return out
