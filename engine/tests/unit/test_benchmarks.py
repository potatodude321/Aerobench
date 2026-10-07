"""Benchmark evaluation: band checks, comparisons and honest status labels."""

from __future__ import annotations

import pytest

from aerobench.validation.benchmarks import BENCHMARKS, evaluate, list_benchmarks


def run(cd, cl=0.35, status="completed", conv="converged"):
    return {"status": status, "metrics": {"metrics": {"Cd": cd, "Cl": cl}}, "convergence": {"status": conv}}


def test_catalogue_has_sources_for_every_reference():
    for b in list_benchmarks():
        assert b["references"], b["id"]
        assert all(r["source"] for r in b["references"])


@pytest.mark.parametrize("cd,conv,status,expected", [
    (0.30, "converged", "completed", "pass"),
    (0.30, "converged_oscillating", "completed", "pass"),
    (0.36, "converged", "completed", "outside_band"),
    (0.30, "not_converged", "completed", "unconverged"),  # in band but unconverged is never a pass
    (0.30, "converged", "failed", "not_run"),
    (float("nan"), "converged", "completed", "outside_band"),
])
def test_status(cd, conv, status, expected):
    assert evaluate("ahmed25", run(cd, status=status, conv=conv))["status"] == expected


def test_comparisons_are_signed_percentages_against_each_source():
    out = evaluate("ahmed25", run(0.3354, cl=0.3557))
    by = {(c["quantity"], c["reference"]): c["difference_pct"] for c in out["comparisons"]}
    assert by[("Cd", 0.285)] == pytest.approx(17.68, abs=0.01)
    assert by[("Cd", 0.299)] == pytest.approx(12.17, abs=0.01)
    assert by[("Cl", 0.345)] == pytest.approx(3.10, abs=0.01)
    assert out["caveats"] == BENCHMARKS["ahmed25"].caveats
