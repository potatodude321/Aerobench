"""Convergence assessment from multiple indicators (BUILD_SPEC §15).

A run is never declared converged on residuals alone.  Indicators:

* force-coefficient settling over a moving window (standard deviation and linear drift,
  in *counts*: 1 count = 0.001);
* residual reduction of pressure and momentum;
* continuity error;
* divergence markers (NaN, exploding residuals, absurd coefficients).

Thresholds are explicit, versioned and reported with every assessment.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

CRITERIA_VERSION = "1"
THRESHOLDS = {
    # metric: (stable_std, stable_drift, bounded_std, bounded_drift)  in coefficient units
    "Cd": (0.002, 0.002, 0.010, 0.004),
    "Cl": (0.005, 0.005, 0.020, 0.008),
    "Cl_front": (0.005, 0.005, 0.020, 0.008),
    "Cl_rear": (0.005, 0.005, 0.020, 0.008),
}
RESIDUAL_DROP_ORDERS = 2.5
RESIDUAL_ABS = 1e-4
MIN_WINDOW = 50


@dataclass
class Criterion:
    name: str
    value: Optional[float]
    threshold: Optional[float]
    passed: Optional[bool]
    note: str = ""


@dataclass
class CoefficientStats:
    mean: float
    std: float
    drift: float  # change of the linear fit across the window
    min: float
    max: float
    last: float
    state: str  # stable | bounded_oscillation | oscillating | drifting | unknown


@dataclass
class ConvergenceAssessment:
    status: str  # converged | converged_oscillating | not_converged | diverged | insufficient_data
    iterations: int
    window_start: Optional[float]
    window_iterations: int
    coefficients: dict[str, CoefficientStats] = field(default_factory=dict)
    residuals: dict[str, dict[str, float]] = field(default_factory=dict)
    continuity: dict[str, float] = field(default_factory=dict)
    criteria: list[Criterion] = field(default_factory=list)
    summary: str = ""
    criteria_version: str = CRITERIA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def window_slice(n: int, fraction: float) -> slice:
    w = max(MIN_WINDOW, int(round(n * fraction)))
    w = min(w, n)
    return slice(n - w, n)


def coefficient_stats(t: np.ndarray, y: np.ndarray, metric: str) -> CoefficientStats:
    y = np.asarray(y, float)
    if len(y) < 3 or not np.all(np.isfinite(y)):
        return CoefficientStats(float(np.nanmean(y)) if len(y) else math.nan, math.nan, math.nan, math.nan, math.nan,
                                float(y[-1]) if len(y) else math.nan, "unknown")
    A = np.vstack([t - t.mean(), np.ones_like(t)]).T
    slope, _ = np.linalg.lstsq(A, y, rcond=None)[0]
    drift = float(slope * (t[-1] - t[0]))
    std = float(np.std(y))
    st, sd, bt, bd = THRESHOLDS.get(metric, (0.005, 0.005, 0.02, 0.008))
    if std <= st and abs(drift) <= sd:
        state = "stable"
    elif std <= bt and abs(drift) <= bd:
        state = "bounded_oscillation"
    elif abs(drift) > bd:
        state = "drifting"  # a trend: more iterations may settle it
    else:
        state = "oscillating"  # no trend but a large swing: typically unsteady separated flow

    return CoefficientStats(float(np.mean(y)), std, drift, float(y.min()), float(y.max()), float(y[-1]), state)


def assess(
    time: np.ndarray,
    coeffs: dict[str, np.ndarray],
    residuals: Optional[dict[str, np.ndarray]] = None,
    continuity_cumulative: Optional[np.ndarray] = None,
    continuity_local: Optional[np.ndarray] = None,
    fraction: float = 0.2,
    expected_iterations: Optional[int] = None,
    nan_detected: bool = False,
) -> ConvergenceAssessment:
    time = np.asarray(time, float)
    n = len(time)
    a = ConvergenceAssessment(status="insufficient_data", iterations=int(time[-1]) if n else 0, window_start=None, window_iterations=0)
    if nan_detected or any(np.any(~np.isfinite(v)) for v in coeffs.values()):
        a.status = "diverged"
        a.summary = "The solution diverged (non-finite values)."
        return a
    if n < 2 * MIN_WINDOW:
        a.summary = f"Only {n} iterations available; at least {2 * MIN_WINDOW} are needed to assess convergence."
        return a
    sl = window_slice(n, fraction)
    tw = time[sl]
    a.window_start = float(tw[0])
    a.window_iterations = len(tw)
    states = []
    for m, y in coeffs.items():
        if y is None:
            continue
        cs = coefficient_stats(tw, np.asarray(y, float)[sl], m)
        a.coefficients[m] = cs
        if abs(cs.mean) > 50:
            a.status = "diverged"
            a.summary = f"{m} = {cs.mean:.3g} is not physical; the solution has diverged."
            return a
        st, sd, _, _ = THRESHOLDS.get(m, (0.005, 0.005, 0.02, 0.008))
        a.criteria.append(Criterion(f"{m} std over window", cs.std, st, cs.std <= st))
        a.criteria.append(Criterion(f"{m} drift over window", abs(cs.drift), sd, abs(cs.drift) <= sd))
        states.append(cs.state)
    res_ok = None
    if residuals:
        oks = []
        for f, r in residuals.items():
            r = np.asarray(r, float)
            r = r[np.isfinite(r) & (r > 0)]
            if len(r) == 0:
                continue
            last = float(np.median(r[-min(len(r), 20):]))
            peak = float(r.max())
            drop = math.log10(peak / last) if last > 0 else math.inf
            a.residuals[f] = {"initial_max": peak, "last": last, "drop_orders": drop}
            if f in ("p", "Ux", "U"):
                ok = drop >= RESIDUAL_DROP_ORDERS or last <= RESIDUAL_ABS
                oks.append(ok)
                a.criteria.append(Criterion(f"{f} residual reduction (orders)", drop, RESIDUAL_DROP_ORDERS, ok,
                                            note=f"or below {RESIDUAL_ABS:g}"))
            if last > 1e2:
                a.status = "diverged"
                a.summary = f"The {f} residual is {last:.3g}: the solution diverged."
                return a
        res_ok = all(oks) if oks else None
    if continuity_cumulative is not None and len(continuity_cumulative):
        a.continuity["cumulative_last"] = float(continuity_cumulative[-1])
    if continuity_local is not None and len(continuity_local):
        loc = float(np.max(np.abs(np.asarray(continuity_local)[sl])))
        a.continuity["local_max_window"] = loc
        a.criteria.append(Criterion("continuity (sum local) over window", loc, 1e-3, loc <= 1e-3))
    if not states:
        a.summary = "No force coefficients available."
        return a
    if all(s == "stable" for s in states) and res_ok is not False:
        a.status = "converged"
        a.summary = f"Forces settled within {a.window_iterations} iterations (std and drift below thresholds)."
    elif all(s in ("stable", "bounded_oscillation") for s in states):
        a.status = "converged_oscillating"
        a.summary = ("Forces oscillate within bounded limits without drift; the window-averaged values are reported. "
                     "This is common for steady RANS of bluff vehicles.")
    else:
        a.status = "not_converged"
        drifting = [m for m, c in a.coefficients.items() if c.state == "drifting"]
        swinging = [m for m, c in a.coefficients.items() if c.state == "oscillating"]
        parts = []
        if drifting:
            parts.append(f"Still drifting: {', '.join(drifting)} — run more iterations or check the mesh.")
        if swinging:
            amp = ", ".join(f"{m} ±{a.coefficients[m].std:.3f}" for m in swinging)
            parts.append(f"Large oscillation without a trend: {amp} (standard deviation over the window). This usually means "
                         "unsteady separated flow that steady RANS cannot settle; more iterations will not help — use the "
                         "window average with care, refine the separation region or use a transient method.")
        a.summary = " ".join(parts) or "Convergence criteria not met."
    if expected_iterations and a.iterations < expected_iterations and a.status != "diverged":
        a.summary += f" (stopped at {a.iterations} of {expected_iterations} iterations)"
    return a
