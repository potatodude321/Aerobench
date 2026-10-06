"""Numerics presets → fvSchemes / fvSolution (version-tracked, BUILD_SPEC §10).

Preset version "1" definitions.  Never edit a released version in place: add "2" and
keep "1" reproducible so old projects regenerate identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.numerics import NumericsConfig, NumericsPreset
from ..ast import Dict, T


@dataclass(frozen=True)
class PresetDef:
    u_div: str  # linearUpwind | linearUpwindV | upwind
    turb_div: str  # upwind | linearUpwind | limitedLinear
    grad_u_limit: float
    laplacian_limit: float | None  # None => corrected
    non_orth: int
    consistent: bool
    relax_p: float | None
    relax_u: float
    relax_turb: float
    p_rel_tol: float
    first_order_iterations: int
    description: str


PRESETS_V1: dict[NumericsPreset, PresetDef] = {
    NumericsPreset.ROBUST: PresetDef(
        "linearUpwind", "upwind", 1.0, 0.33, 1, False, 0.3, 0.7, 0.7, 0.05, 300,
        "SIMPLE (p relaxation 0.3), limited gradients and Laplacians, 300 first-order iterations before switching "
        "to second-order momentum. Most tolerant of poor meshes; slowest to converge.",
    ),
    NumericsPreset.BALANCED: PresetDef(
        "linearUpwindV", "upwind", 1.0, 0.33, 0, True, None, 0.9, 0.7, 0.01, 0,
        "SIMPLEC (consistent), second-order bounded linearUpwindV momentum, first-order turbulence. "
        "The automotive default.",
    ),
    NumericsPreset.FAST_CONVERGENCE: PresetDef(
        "linearUpwindV", "upwind", 1.0, 0.33, 0, True, None, 0.95, 0.8, 0.05, 0,
        "SIMPLEC with aggressive relaxation; fastest but may oscillate on difficult meshes.",
    ),
    NumericsPreset.HIGH_FIDELITY: PresetDef(
        "linearUpwindV", "linearUpwind", 0.5, 0.5, 1, True, None, 0.85, 0.7, 0.01, 0,
        "SIMPLEC, second-order momentum and turbulence, lighter gradient limiting, one non-orthogonal "
        "corrector. Requires a good mesh; most accurate.",
    ),
}

PRESETS = {"1": PRESETS_V1}


def preset_def(cfg: NumericsConfig) -> PresetDef:
    table = PRESETS.get(cfg.preset_version)
    if table is None:
        raise ValueError(f"unknown numerics preset version {cfg.preset_version}")
    key = cfg.base_preset if cfg.preset == NumericsPreset.CUSTOM else cfg.preset
    base = table[key]
    o = cfg.overrides
    if not o:
        return base
    fields = {k: o[k] for k in base.__dataclass_fields__ if k in o}
    return PresetDef(**{**base.__dict__, **fields})


def turbulence_fields(model: str) -> list[str]:
    return {
        "kOmegaSST": ["k", "omega"],
        "GEKO": ["k", "omega"],
        "SpalartAllmaras": ["nuTilda"],
        "realizableKE": ["k", "epsilon"],
        "laminar": [],
    }[model]


def fv_schemes(cfg: NumericsConfig, model: str, first_order: bool = False) -> Dict:
    p = preset_def(cfg)
    if p.laplacian_limit is None:
        lap = T("Gauss", "linear", "corrected")
        sn = T("corrected")
    else:
        lap = T("Gauss", "linear", "limited", "corrected", float(p.laplacian_limit))
        sn = T("limited", "corrected", float(p.laplacian_limit))
    d = Dict()
    d["ddtSchemes"] = Dict(default="steadyState")
    grad = Dict(default=T("Gauss", "linear"))
    grad["grad(U)"] = T("cellLimited", "Gauss", "linear", p.grad_u_limit)
    for f in turbulence_fields(model):
        grad[f"grad({f})"] = T("cellLimited", "Gauss", "linear", 1)
    d["gradSchemes"] = grad
    div = Dict(default="none")
    if first_order:
        div["div(phi,U)"] = T("bounded", "Gauss", "upwind")
    elif p.u_div == "linearUpwindV":
        div["div(phi,U)"] = T("bounded", "Gauss", "linearUpwindV", "grad(U)")
    elif p.u_div == "linearUpwind":
        div["div(phi,U)"] = T("bounded", "Gauss", "linearUpwind", "grad(U)")
    else:
        div["div(phi,U)"] = T("bounded", "Gauss", "upwind")
    for f in turbulence_fields(model):
        if p.turb_div == "linearUpwind" and not first_order:
            div[f"div(phi,{f})"] = T("bounded", "Gauss", "linearUpwind", f"grad({f})")
        elif p.turb_div == "limitedLinear" and not first_order:
            div[f"div(phi,{f})"] = T("bounded", "Gauss", "limitedLinear", 1)
        else:
            div[f"div(phi,{f})"] = T("bounded", "Gauss", "upwind")
    div["div((nuEff*dev2(T(grad(U)))))"] = T("Gauss", "linear")
    d["divSchemes"] = div
    d["laplacianSchemes"] = Dict(default=lap)
    d["interpolationSchemes"] = Dict(default="linear")
    d["snGradSchemes"] = Dict(default=sn)
    d["wallDist"] = Dict(method="meshWave")
    return d


def fv_solution(cfg: NumericsConfig, model: str) -> Dict:
    p = preset_def(cfg)
    o: dict[str, Any] = cfg.overrides
    solvers = Dict()
    solvers["p"] = Dict(solver="GAMG", smoother="GaussSeidel", tolerance=1e-7, relTol=p.p_rel_tol, nCellsInCoarsestLevel=50)
    solvers["Phi"] = Dict(solver="GAMG", smoother="GaussSeidel", tolerance=1e-6, relTol=0.01)
    turb = turbulence_fields(model)
    rx = "(U|" + "|".join(turb) + ")" if turb else "U"
    solvers[f'"{rx}"'] = Dict(solver="smoothSolver", smoother="symGaussSeidel", tolerance=1e-8, relTol=0.1, nSweeps=1)
    d = Dict()
    d["solvers"] = solvers
    simple = Dict(nNonOrthogonalCorrectors=int(o.get("non_orth", p.non_orth)), consistent=bool(p.consistent))
    d["SIMPLE"] = simple
    d["potentialFlow"] = Dict(nNonOrthogonalCorrectors=10)
    relax = Dict()
    if p.relax_p is not None:
        relax["fields"] = Dict(p=float(p.relax_p))
    eq = Dict(U=float(p.relax_u))
    for f in turb:
        eq[f] = float(p.relax_turb)
    relax["equations"] = eq
    d["relaxationFactors"] = relax
    d["cache"] = Dict({"grad(U)": T()})
    return d
