"""Grid-convergence index against the worked example of Celik et al. (2008), Table 1."""

import math

import pytest

from aerobench.validation.gci import gci


def test_celik_2008_example():
    # 2-D example: N = 18000, 8000, 4500 cells -> h = N^(-1/2); phi = 6.063, 5.972, 5.863
    h = [18000 ** -0.5, 8000 ** -0.5, 4500 ** -0.5]
    r = gci(h, [6.063, 5.972, 5.863])
    assert r.status == "monotonic"
    assert r.r21 == pytest.approx(1.5, rel=1e-6) and r.r32 == pytest.approx(math.sqrt(8000 / 4500), rel=1e-6)
    assert r.order == pytest.approx(1.53, abs=0.01)
    assert r.extrapolated == pytest.approx(6.1685, abs=2e-3)
    assert r.e21_rel == pytest.approx(0.015, abs=5e-4)
    assert r.gci_fine == pytest.approx(0.0217, abs=5e-4)


def test_exact_second_order_sequence():
    h = [0.01, 0.02, 0.04]
    phi = [1 + 3 * x**2 for x in h]
    r = gci(list(reversed(h)), list(reversed(phi)))  # order of input does not matter
    assert r.order == pytest.approx(2.0, abs=1e-6)
    assert r.extrapolated == pytest.approx(1.0, abs=1e-9)


def test_oscillatory_and_degenerate_cases():
    assert gci([1, 2, 3], [1.0, 1.1, 1.05]).status == "oscillatory"
    assert gci([1, 2, 3], [1.0, 1.0, 1.1]).status == "insufficient"
    with pytest.raises(ValueError):
        gci([1, 2], [1, 2])
