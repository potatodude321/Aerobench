"""Structured result ingestion from a run case (forces → automotive metrics)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..aero.forces import AeroResult, ReferenceSpec, metrics_dict, reduce_forces
from ..domain.project import ProjectConfig
from ..domain.setup import Symmetry
from ..foam.logs.datfiles import read_series, read_yplus
from ..monitoring.convergence import ConvergenceAssessment, assess, window_slice


@dataclass
class ForceHistory:
    time: np.ndarray
    force: np.ndarray  # (n, 3) total
    moment: np.ndarray  # (n, 3) about cofr
    cofr: tuple[float, float, float]
    pressure_force: Optional[np.ndarray] = None
    viscous_force: Optional[np.ndarray] = None


def _cofr_from_file(path: Path) -> Optional[tuple[float, float, float]]:
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines()[:20]:
        if "CofR" in line and "(" in line:
            vals = line[line.index("(") + 1 : line.index(")")].split()
            return tuple(float(v) for v in vals)  # type: ignore[return-value]
    return None


def load_force_history(case_dir: Path, fo_name: str = "forces_total",
                       default_cofr: Optional[tuple[float, float, float]] = None) -> Optional[ForceHistory]:
    """Force/moment history of a ``forces`` function object.  Moments are about the CofR in
    the file header; if the header lacks it (other forks/versions), ``default_cofr`` — the
    moment centre the case was generated with — is used instead of silently assuming the
    origin."""
    d = Path(case_dir) / "postProcessing" / fo_name
    f = read_series(d, "force.dat")
    m = read_series(d, "moment.dat")
    if f is None or m is None or not len(f) or not len(m):
        return None
    fn, mn = f.numeric(), m.numeric()
    tf, tm = fn["Time"], mn["Time"]
    common, i_f, i_m = np.intersect1d(tf, tm, return_indices=True)
    F = np.column_stack([fn["total_x"], fn["total_y"], fn["total_z"]])[i_f]
    M = np.column_stack([mn["total_x"], mn["total_y"], mn["total_z"]])[i_m]
    P = V = None
    if "pressure_x" in fn:
        P = np.column_stack([fn["pressure_x"], fn["pressure_y"], fn["pressure_z"]])[i_f]
        V = np.column_stack([fn["viscous_x"], fn["viscous_y"], fn["viscous_z"]])[i_f]
    first = sorted((d).glob("*/force.dat"))
    cofr = _cofr_from_file(first[0]) if first else None
    if cofr is None:
        if default_cofr is None:
            return None  # moments would be about an unknown point
        cofr = tuple(float(v) for v in default_cofr)  # type: ignore[assignment]
    return ForceHistory(common, F, M, cofr, P, V)


def reference_spec(cfg: ProjectConfig, ground_z: float) -> ReferenceSpec:
    r = cfg.reference
    return ReferenceSpec(
        area=float(r.area or math.nan),
        length=float(r.length or math.nan),
        # forces are written with rhoInf = fluid density; the coefficients must use the same value
        density=float(cfg.physics.fluid.density),
        velocity=float(r.velocity or cfg.operating.speed),
        moment_centre=tuple(r.moment_centre or (0.0, 0.0, ground_z)),
        axes=r.axes.value,
        yaw_deg=cfg.operating.yaw_deg,
        wheelbase=cfg.vehicle.wheelbase,
        axle_reference=cfg.vehicle.wheelbase_mid_ground(ground_z),
        half_model=cfg.tunnel.symmetry == Symmetry.HALF,
    )


def coefficient_series(h: ForceHistory, ref: ReferenceSpec) -> dict[str, np.ndarray]:
    keys = ["Cd", "Cl", "Cs", "Cm_pitch", "Cl_front", "Cl_rear", "balance_front_pct", "drag_N", "downforce_N"]
    out: dict[str, list] = {k: [] for k in keys}
    for F, M in zip(h.force, h.moment):
        r = reduce_forces(F, M, h.cofr, ref)
        md = metrics_dict(r)
        for k in keys:
            v = md.get(k)
            out[k].append(math.nan if v is None else v)
    return {k: np.asarray(v, float) for k, v in out.items()}


def window_mean_result(h: ForceHistory, ref: ReferenceSpec, fraction: float) -> tuple[AeroResult, slice]:
    sl = window_slice(len(h.time), fraction)
    F = h.force[sl].mean(axis=0)
    M = h.moment[sl].mean(axis=0)
    return reduce_forces(F, M, h.cofr, ref), sl


def component_breakdown(case_dir: Path, cfg: ProjectConfig, ref: ReferenceSpec, fraction: float) -> dict[str, dict[str, Any]]:
    out = {}
    for c in cfg.geometry.components:
        h = load_force_history(case_dir, f"forces_{c.id}", ref.moment_centre)
        if h is None or len(h.time) == 0:
            continue
        r, _ = window_mean_result(h, ref, fraction)
        md = metrics_dict(r)
        out[c.id] = {"name": c.name, "role": c.role.value, **{k: md[k] for k in ("Cd", "Cl", "Cs", "drag_N", "downforce_N", "side_N")}}
    return out


def residual_series(case_dir: Path) -> dict[str, np.ndarray]:
    t = read_series(Path(case_dir) / "postProcessing" / "solverInfo", "solverInfo.dat")
    if t is None:
        return {}
    num = t.numeric()
    out = {"Time": num["Time"]}
    for c, v in num.items():
        if c.endswith("_initial"):
            out[c[: -len("_initial")]] = v
    return out


def continuity_series(case_dir: Path) -> dict[str, np.ndarray]:
    t = read_series(Path(case_dir) / "postProcessing" / "continuityError", "continuityError.dat")
    return t.numeric() if t is not None else {}


def ingest_run_results(case_dir: Path, cfg: ProjectConfig, ground_z: float, expected_iterations: Optional[int] = None,
                       nan_detected: bool = False) -> dict[str, Any]:
    """Compute everything the results dashboard needs from a completed (or partial) run."""
    ref = reference_spec(cfg, ground_z)
    h = load_force_history(case_dir, default_cofr=ref.moment_centre)
    out: dict[str, Any] = {"reference": {"area": ref.area, "length": ref.length, "density": ref.density, "velocity": ref.velocity,
                                         "moment_centre": list(ref.moment_centre), "axes": ref.axes, "wheelbase": ref.wheelbase,
                                         "axle_reference": list(ref.axle_reference) if ref.axle_reference else None,
                                         "half_model": ref.half_model, "q": ref.q}}
    if h is None or len(h.time) == 0:
        out["available"] = False
        return out
    fraction = cfg.numerics.averaging_fraction
    mean_res, sl = window_mean_result(h, ref, fraction)
    last_res = reduce_forces(h.force[-1], h.moment[-1], h.cofr, ref)
    series = coefficient_series(h, ref)
    res = residual_series(case_dir)
    cont = continuity_series(case_dir)
    resid_for_assess = {k: v for k, v in res.items() if k != "Time"}
    conv: ConvergenceAssessment = assess(
        h.time,
        {k: series[k] for k in ("Cd", "Cl", "Cl_front", "Cl_rear") if np.all(np.isfinite(series[k]))},
        resid_for_assess,
        cont.get("Cumulative"),
        cont.get("Local"),
        fraction,
        expected_iterations,
        nan_detected,
    )
    yplus = read_yplus(Path(case_dir) / "postProcessing" / "yPlus")
    pv = None
    if h.pressure_force is not None:
        from ..aero.forces import _axes

        Fp = h.pressure_force[sl].mean(axis=0)
        Fv = h.viscous_force[sl].mean(axis=0)
        dd, _, _ = _axes(ref.axes, ref.yaw_deg)  # same drag axis and half-model factor as the total Cd
        k = 2.0 if ref.half_model else 1.0
        q_a = ref.q * ref.area
        pv = {"Cd_pressure": float(k * (Fp @ dd) / q_a), "Cd_viscous": float(k * (Fv @ dd) / q_a)}
    out.update({
        "available": True,
        "iterations": int(h.time[-1]),
        "averaging_window": [float(h.time[sl][0]), float(h.time[sl][-1])],
        "metrics": metrics_dict(mean_res),
        "metrics_last": metrics_dict(last_res),
        "result": mean_res.as_dict(),
        "pressure_viscous": pv,
        "components": component_breakdown(case_dir, cfg, ref, fraction),
        "convergence": conv.as_dict(),
        "yplus": yplus,
    })
    return out


def series_payload(case_dir: Path, cfg: ProjectConfig, ground_z: float, max_points: int = 4000) -> dict[str, Any]:
    """Time series for plots (decimated uniformly when long)."""
    ref = reference_spec(cfg, ground_z)
    out: dict[str, Any] = {}
    h = load_force_history(case_dir, default_cofr=ref.moment_centre)
    if h is not None and len(h.time):
        s = coefficient_series(h, ref)
        idx = _decimate_idx(len(h.time), max_points)
        out["forces"] = {"time": h.time[idx].tolist(), **{k: _clean(v[idx]) for k, v in s.items()}}
    r = residual_series(case_dir)
    if r:
        idx = _decimate_idx(len(r["Time"]), max_points)
        out["residuals"] = {k if k != "Time" else "time": _clean(v[idx]) for k, v in r.items()}
    c = continuity_series(case_dir)
    if c:
        idx = _decimate_idx(len(c["Time"]), max_points)
        out["continuity"] = {k.lower(): _clean(v[idx]) for k, v in c.items()}
    return out


def _decimate_idx(n: int, m: int) -> np.ndarray:
    if n <= m:
        return np.arange(n)
    idx = np.unique(np.linspace(0, n - 1, m).round().astype(int))
    return idx


def _clean(a: np.ndarray) -> list:
    return [None if not np.isfinite(x) else float(x) for x in np.asarray(a, float)]
