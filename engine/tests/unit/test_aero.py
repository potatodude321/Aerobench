import math

import numpy as np
import pytest

from aerobench.aero.flow import (
    air_properties,
    first_cell_height,
    inlet_turbulence,
    reynolds,
    skin_friction,
    yplus_from_height,
)
from aerobench.aero.forces import ReferenceSpec, freestream_direction, reduce_forces, transfer_moment


def test_air_properties_standard_day():
    p = air_properties(15.0, 101325.0)
    assert p["density"] == pytest.approx(1.225, rel=2e-3)
    assert p["dynamic_viscosity"] == pytest.approx(1.789e-5, rel=5e-3)
    p20 = air_properties(20.0)
    assert p20["kinematic_viscosity"] == pytest.approx(1.516e-5, rel=1e-2)


def test_reynolds():
    assert reynolds(40.0, 4.5, 1.5e-5) == pytest.approx(1.2e7)


def test_skin_friction_correlations():
    # Schlichting 1979 at Re=1e7: (2*7-0.65)^-2.3
    assert skin_friction(1e7) == pytest.approx((13.35) ** -2.3)
    assert skin_friction(1e6, "power_law_1_7") == pytest.approx(0.0592 * 1e6**-0.2)
    with pytest.raises(ValueError):
        skin_friction(1e6, "nope")


def test_first_cell_height_roundtrip():
    ws = first_cell_height(50, 40.0, 4.5, 1.5e-5)
    assert 0.3e-3 < ws.first_cell_centre_height < 1.0e-3  # sanity for a car at 40 m/s
    assert ws.first_cell_height == pytest.approx(2 * ws.first_cell_centre_height)
    assert yplus_from_height(ws.first_cell_height, 40.0, 4.5, 1.5e-5) == pytest.approx(50.0)
    assert "Re_L" in ws.assumptions


def test_inlet_turbulence_viscosity_ratio_and_length_scale():
    t = inlet_turbulence(40.0, 1.5e-5, intensity=0.01, viscosity_ratio=10.0)
    assert t.k == pytest.approx(1.5 * 0.4**2)
    assert t.nut == pytest.approx(1.5e-4)
    assert t.omega == pytest.approx(t.k / t.nut)
    t2 = inlet_turbulence(40.0, 1.5e-5, intensity=0.01, viscosity_ratio=None, length_scale=t.length_scale)
    assert t2.omega == pytest.approx(t.omega, rel=1e-9)


def test_transfer_moment():
    F = np.array([0.0, 0.0, 10.0])
    M_O = np.zeros(3)
    O = np.array([1.0, 0.0, 0.0])
    P = np.zeros(3)
    # force +z at x=1 about origin: r x F = (1,0,0) x (0,0,10) = (0,-10,0)
    assert np.allclose(transfer_moment(M_O, F, O, P), [0.0, -10.0, 0.0])


def _ref(**kw):
    base = dict(area=2.0, length=2.5, density=1.2, velocity=40.0, moment_centre=(0.0, 0.0, 0.0), wheelbase=2.5,
                axle_reference=(0.0, 0.0, 0.0))
    base.update(kw)
    return ReferenceSpec(**base)


def test_pure_drag_and_downforce_signs():
    q = 0.5 * 1.2 * 40**2
    F = np.array([0.3 * q * 2.0, 0.0, -0.5 * q * 2.0])  # rearward drag, downforce
    r = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref())
    assert r.cd == pytest.approx(0.3)
    assert r.cl == pytest.approx(-0.5)
    assert r.downforce > 0
    # no moment => equal split
    assert r.lift_front == pytest.approx(r.lift / 2)
    assert r.balance_front_pct == pytest.approx(50.0)
    assert not r.balance_ill_conditioned


def test_front_biased_downforce_gives_front_balance():
    # Downforce applied at the front axle (x = -1.25, nose at -x).  Moment about origin:
    # r x F = (-1.25,0,0) x (0,0,-1000) = (0,-1250,0)... check: (ry*Fz - rz*Fy, rz*Fx - rx*Fz, ...)
    F = np.array([0.0, 0.0, -1000.0])
    r_app = np.array([-1.25, 0.0, 0.0])
    M = np.cross(r_app, F)
    res = reduce_forces(F, M, (0, 0, 0), _ref())
    # pitch: a downforce at the nose is nose-DOWN -> negative pitch
    assert res.pitch < 0
    assert res.downforce_front == pytest.approx(1000.0)
    assert res.downforce_rear == pytest.approx(0.0, abs=1e-9)
    assert res.balance_front_pct == pytest.approx(100.0)


def test_ill_conditioned_balance_is_missing_in_metrics():
    from aerobench.aero.forces import metrics_dict

    # net lift: front lifts, rear presses down slightly -> a "balance" of hundreds of percent
    F = np.array([0.0, 0.0, 100.0])
    M = np.cross(np.array([-1.25, 0.0, 0.0]), np.array([0.0, 0.0, 300.0]))
    res = reduce_forces(F, M, (0, 0, 0), _ref())
    assert res.balance_ill_conditioned
    assert metrics_dict(res)["balance_front_pct"] is None
    ok = reduce_forces(np.array([0.0, 0.0, -1000.0]), np.zeros(3), (0, 0, 0), _ref())
    assert metrics_dict(ok)["balance_front_pct"] == pytest.approx(50.0)


def test_drag_at_height_produces_nose_up_moment_about_ground_and_rear_unloading():
    # Drag (+x, rearward) acting at height h above the ground reference: tends to pitch the
    # nose UP (top pushed rearward) => front axle unloaded (positive lift on front).
    F = np.array([500.0, 0.0, 0.0])
    r_app = np.array([0.0, 0.0, 0.6])
    M = np.cross(r_app, F)
    res = reduce_forces(F, M, (0, 0, 0), _ref())
    assert res.pitch > 0
    assert res.lift_front > 0 and res.lift_rear < 0
    assert res.lift_front + res.lift_rear == pytest.approx(0.0, abs=1e-9)


def test_side_force_and_yaw_signs():
    # Force toward the vehicle right (+y) applied at the nose (x=-2): the nose is pushed
    # right => yawing moment nose-right positive.
    F = np.array([0.0, 100.0, 0.0])
    M = np.cross([-2.0, 0.0, 0.0], F)
    res = reduce_forces(F, M, (0, 0, 0), _ref())
    assert res.side == pytest.approx(100.0)
    assert res.yaw > 0


def test_roll_sign_right_side_down():
    # Downward force on the right side (+y) => right side down => positive roll
    F = np.array([0.0, 0.0, -100.0])
    M = np.cross([0.0, 0.8, 0.0], F)
    res = reduce_forces(F, M, (0, 0, 0), _ref())
    assert res.roll > 0


def test_wind_axes_under_yaw():
    d = freestream_direction(10.0)
    assert d[1] < 0  # wind from the right travels towards the vehicle left (-y)
    F = 100.0 * d  # pure force along the flow
    res = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref(axes="wind", yaw_deg=10.0))
    assert res.drag == pytest.approx(100.0)
    assert res.side == pytest.approx(0.0, abs=1e-9)
    res_b = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref(axes="body", yaw_deg=10.0))
    assert res_b.drag == pytest.approx(100 * math.cos(math.radians(10)))
    assert res_b.side < 0  # pushed towards vehicle left


def test_balance_ill_conditioned_when_net_lift():
    F = np.array([0.0, 0.0, 200.0])
    res = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref())
    assert res.balance_ill_conditioned


def test_half_model_doubles_symmetric_quantities():
    F = np.array([10.0, 3.0, -20.0])
    full = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref())
    half = reduce_forces(F, np.zeros(3), (0, 0, 0), _ref(half_model=True))
    assert half.drag == pytest.approx(2 * full.drag)
    assert half.lift == pytest.approx(2 * full.lift)
    assert half.side == 0.0
