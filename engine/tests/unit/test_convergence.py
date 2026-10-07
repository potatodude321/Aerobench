"""Convergence assessment (docs/validation.md, criteria version 1).

Synthetic coefficient histories with known properties: a run must only be called converged
when every criterion passes, and the summary must name the right problem.
"""

from __future__ import annotations

import numpy as np
import pytest

from aerobench.monitoring.convergence import CRITERIA_VERSION, assess, coefficient_stats

N = 1000
T = np.arange(1, N + 1, dtype=float)


def settle(final: float, amp: float = 0.0, period: float = 37.0, trend: float = 0.0, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """Exponential approach to ``final`` plus an oscillation, a linear trend and noise."""
    rng = np.random.default_rng(seed)
    return (final + 0.3 * np.exp(-T / 60.0) + amp * np.sin(2 * np.pi * T / period) + trend * (T - T[-1]) / N
            + noise * rng.standard_normal(N))


def falling_residuals(orders: float) -> np.ndarray:
    return np.logspace(0, -orders, N)


def test_settled_forces_and_residuals_are_converged():
    a = assess(T, {"Cd": settle(0.30, amp=0.0005), "Cl": settle(-0.10, amp=0.001)},
               residuals={"p": falling_residuals(4), "Ux": falling_residuals(4)}, continuity_local=np.full(N, 1e-5))
    assert a.status == "converged", a.summary
    assert a.window_iterations == 200 and a.window_start == 801
    assert all(c.passed for c in a.criteria)
    assert a.coefficients["Cd"].mean == pytest.approx(0.30, abs=1e-3)
    assert a.criteria_version == CRITERIA_VERSION


def test_bounded_oscillation_is_reported_as_such():
    a = assess(T, {"Cd": settle(0.30, amp=0.008), "Cl": settle(0.1, amp=0.015)}, residuals={"p": falling_residuals(3)})
    assert a.status == "converged_oscillating"
    assert {c.state for c in a.coefficients.values()} == {"bounded_oscillation"}
    assert "window-averaged" in a.summary


def test_trend_is_drifting_and_names_the_quantity():
    a = assess(T, {"Cd": settle(0.30, trend=0.05), "Cl": settle(0.1)})
    assert a.status == "not_converged"
    assert a.coefficients["Cd"].state == "drifting"
    assert a.coefficients["Cl"].state == "stable"
    assert "drifting: Cd" in a.summary and "Cl" not in a.summary.split("drifting:")[1].split("—")[0]


def test_large_swing_without_trend_is_oscillating_not_drifting():
    # the medium Ahmed 25° case: Cl swinging ±0.04 with no trend
    a = assess(T, {"Cd": settle(0.335, amp=0.004), "Cl": settle(0.355, amp=0.06)})
    assert a.status == "not_converged"
    assert a.coefficients["Cl"].state == "oscillating"
    assert "drifting" not in a.summary.lower().split("large")[0]
    assert "Large oscillation without a trend: Cl" in a.summary
    assert "more iterations will not help" in a.summary


def test_residuals_block_converged_but_not_bounded():
    coeffs = {"Cd": settle(0.30, amp=0.0005)}
    a = assess(T, coeffs, residuals={"p": falling_residuals(1.5)})
    assert a.status == "converged_oscillating"  # documented: bounded status does not require residual reduction
    assert any(c.name.startswith("p residual") and c.passed is False for c in a.criteria)
    b = assess(T, coeffs, residuals={"p": np.full(N, 5e-5)})  # below the absolute floor
    assert b.status == "converged"


@pytest.mark.parametrize("bad", ["nan", "huge", "residual"])
def test_divergence(bad):
    cd = settle(0.3)
    res = {"p": falling_residuals(3)}
    if bad == "nan":
        cd[-5] = np.nan
    elif bad == "huge":
        cd[-300:] = 1e4
    else:
        res = {"p": np.logspace(0, 4, N)}
    a = assess(T, {"Cd": cd}, residuals=res)
    assert a.status == "diverged"


def test_short_history_is_insufficient():
    a = assess(T[:80], {"Cd": settle(0.3)[:80]})
    assert a.status == "insufficient_data"
    assert "80 iterations" in a.summary


def test_stopped_early_is_noted():
    a = assess(T, {"Cd": settle(0.30)}, expected_iterations=2000)
    assert "stopped at 1000 of 2000" in a.summary


def test_window_fraction_and_minimum():
    a = assess(T, {"Cd": settle(0.3)}, fraction=0.01)
    assert a.window_iterations == 50  # MIN_WINDOW
    b = assess(T, {"Cd": settle(0.3)}, fraction=0.5)
    assert b.window_iterations == 500


def test_coefficient_stats_drift_is_change_across_window():
    t = np.arange(100, dtype=float)
    s = coefficient_stats(t, 0.3 + 0.0001 * t, "Cd")
    assert s.drift == pytest.approx(0.0099, rel=1e-6)
    assert s.state == "drifting"
