"""Geometry parameterisation: rigid moves, FFD, RBF, symmetry and validity checks."""

from __future__ import annotations

import numpy as np
import pytest

from aerobench.domain.exploration import FFDDef, GeometryParameter, RBFDef, RotateDef, TranslateDef
from aerobench.geometry.components import identify_components
from aerobench.geometry.deform import (
    apply_parameters,
    check_deformation,
    displacement_field,
    edge_triangle_hits,
    ffd_displacement,
    rbf_solve,
)
from aerobench.geometry.fixtures import box, generic_car, sphere
from aerobench.geometry.trisurface import TriSurface


@pytest.fixture(scope="module")
def car():
    s = generic_car(rear_wing=True)
    ident = identify_components(s)
    return s, ident.components, ident.wheels


def _param(**kw):
    base = dict(id="p", name="p", lower=-1.0, upper=1.0, baseline=0.0)
    base.update(kw)
    return GeometryParameter.model_validate(base)


def test_baseline_value_is_bit_identical(car):
    s, comps, wheels = car
    p = _param(deformation=TranslateDef(direction=(0, 0, 1)), components=["wing"])
    r = apply_parameters(s, comps, wheels, [p], {"p": 0.0})
    assert np.array_equal(r.surface.points, s.points)
    assert check_deformation(s, r, [p]).valid


def test_translate_moves_only_selected_component(car):
    s, comps, wheels = car
    p = _param(deformation=TranslateDef(direction=(0, 0, 1)), components=["wing"], lower=-0.1, upper=0.1)
    r = apply_parameters(s, comps, wheels, [p], {"p": 0.05})
    wing_idx = s.region_names.index("wing")
    wing_v = np.unique(s.faces[s.regions == wing_idx])
    other_v = np.setdiff1d(np.arange(s.n_points), wing_v)
    assert np.allclose(r.surface.points[wing_v] - s.points[wing_v], [0, 0, 0.05])
    assert np.array_equal(r.surface.points[other_v], s.points[other_v])
    chk = check_deformation(s, r, [p])
    assert chk.valid, chk.errors
    assert chk.stats["max_displacement"] == pytest.approx(0.05)


def test_value_outside_bounds_is_rejected(car):
    s, comps, wheels = car
    p = _param(deformation=TranslateDef(direction=(0, 0, 1)), components=["wing"], lower=-0.1, upper=0.1)
    with pytest.raises(ValueError):
        apply_parameters(s, comps, wheels, [p], {"p": 0.2})


def test_rigid_wheel_move_updates_wheel_centre(car):
    s, comps, wheels = car
    fr = next(w for w in wheels if w.position.value == "FR")
    p = _param(deformation=TranslateDef(direction=(1, 0, 0)), components=[fr.component_id], lower=-0.1, upper=0.1)
    r = apply_parameters(s, comps, wheels, [p], {"p": 0.03})
    w2 = next(w for w in r.wheels if w.position.value == "FR")
    assert w2.centre[0] == pytest.approx(fr.centre[0] + 0.03)
    other = next(w for w in r.wheels if w.position.value == "FL")
    assert other.centre == next(w for w in wheels if w.position.value == "FL").centre


def test_wheels_cannot_be_morphed(car):
    s, comps, wheels = car
    wid = wheels[0].component_id
    p = _param(deformation=FFDDef(box_min=(0, -1, 0), box_max=(1, 1, 1), moves=[((1, 1, 1), (0, 0, 1))]), components=[wid])
    with pytest.raises(ValueError, match="rigidly"):
        apply_parameters(s, comps, wheels, [p], {"p": 0.5})


def test_rotation_about_pivot():
    pts = np.array([[1.0, 0, 0], [0, 0, 0]])
    p = _param(deformation=RotateDef(pivot=(0, 0, 0), axis=(0, 0, 1)), lower=-180, upper=180)
    d = displacement_field(p, 90.0)(pts)
    assert np.allclose(pts + d, [[0, 1, 0], [0, 0, 0]], atol=1e-12)


def test_ffd_zero_on_box_boundary_and_outside():
    d = FFDDef(box_min=(0, 0, 0), box_max=(1, 1, 1), divisions=(2, 2, 2), moves=[((1, 1, 1), (0, 0, 1))])
    pts = np.array([[0.5, 0.5, 0.5], [0.0, 0.5, 0.5], [1.0, 0.3, 0.2], [2.0, 0.5, 0.5], [0.5, 0.5, 1.0]])
    disp = ffd_displacement(pts, d, 1.0)
    # centre: B_1^2(0.5)^3 = 0.5^3
    assert disp[0] == pytest.approx([0, 0, 0.125])
    assert np.allclose(disp[1:], 0.0)
    # linear in value
    assert np.allclose(ffd_displacement(pts, d, -2.0), -2.0 * disp)


def test_ffd_boundary_control_points_rejected_when_frozen():
    d = FFDDef(box_min=(0, 0, 0), box_max=(1, 1, 1), divisions=(2, 2, 2), moves=[((0, 1, 1), (0, 0, 1))])
    with pytest.raises(ValueError, match="boundary"):
        ffd_displacement(np.zeros((1, 3)) + 0.5, d, 1.0)


@pytest.mark.parametrize("basis", ["wendland_c2", "thin_plate"])
def test_rbf_interpolates_handles_and_fixed_points(basis):
    d = RBFDef(handles=[((0.0, 0.0, 0.0), (0.0, 0.0, 0.1)), ((0.5, 0.0, 0.0), (0.0, 0.05, 0.0))],
               fixed_points=[(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], support_radius=2.0, basis=basis)
    sol = rbf_solve(d, 2.0)
    got = sol(np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))
    assert np.allclose(got, [[0, 0, 0.2], [0, 0.1, 0], [0, 0, 0], [0, 0, 0]], atol=1e-9)


def test_wendland_support_is_compact():
    d = RBFDef(handles=[((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))], support_radius=0.5)
    sol = rbf_solve(d, 1.0)
    assert np.allclose(sol(np.array([[0.6, 0, 0], [0, 0, 2.0]])), 0.0)


def test_symmetric_field_is_exactly_mirror_symmetric():
    # a handle on +y only; symmetrised field must satisfy d(x,-y,z) = M d(x,y,z)
    p = _param(deformation=RBFDef(handles=[((0.0, 0.4, 0.0), (0.0, 0.1, 0.05))], support_radius=1.0), symmetric_about_centreline=True)
    f = displacement_field(p, 1.0)
    rng = np.random.default_rng(0)
    x = rng.uniform(-0.5, 0.5, (50, 3))
    xm = x * [1, -1, 1]
    assert np.allclose(f(xm), f(x) * [1, -1, 1], atol=1e-14)


def test_fold_is_detected():
    s = box((0, 0, 0), (1, 1, 1), n=4)
    # push the top face's interior down through the bottom: FFD with a huge move
    p = _param(deformation=FFDDef(box_min=(0.1, 0.1, 0.5), box_max=(0.9, 0.9, 1.2), divisions=(2, 2, 2),
                                  moves=[((1, 1, 1), (0, 0, -1))]), lower=-20, upper=20)
    from aerobench.geometry.deform import DeformResult

    d = displacement_field(p, 20.0)(s.points)
    res = DeformResult(TriSurface(s.points + d, s.faces, s.regions, s.region_names), d, [], {"p": float(np.linalg.norm(d, axis=1).max())})
    chk = check_deformation(s, res, [p])
    assert not chk.valid
    codes = {e["code"] for e in chk.errors}
    assert codes & {"geometry.folded", "geometry.self_intersection"}


def test_new_intersection_detected_but_existing_ignored():
    a = sphere(0.5, (0, 0, 0), n_lat=12, n_lon=24, name="a")
    b = sphere(0.3, (1.2, 0, 0), n_lat=12, n_lon=24, name="b")
    s = TriSurface.merge([a, b])
    assert edge_triangle_hits(s.points, s.faces, np.arange(s.n_faces)) == set()
    from aerobench.domain.geometry import ComponentRole, GeometryComponent

    comps = [GeometryComponent(id="a", name="a", role=ComponentRole.BODY, source_regions=["a"]),
             GeometryComponent(id="b", name="b", role=ComponentRole.BODY, source_regions=["b"])]
    p = _param(deformation=TranslateDef(direction=(-1, 0, 0)), components=["b"], lower=0, upper=1)
    ok = apply_parameters(s, comps, [], [p], {"p": 0.2})
    assert check_deformation(s, ok, [p]).valid
    bad = apply_parameters(s, comps, [], [p], {"p": 0.6})
    chk = check_deformation(s, bad, [p])
    assert not chk.valid and chk.errors[0]["code"] == "geometry.self_intersection"
    # an already-intersecting baseline: moving slightly further must not be blamed for old hits
    base = bad.surface
    p2 = _param(deformation=TranslateDef(direction=(0, 0, 1)), components=["a"], lower=0, upper=1)
    tiny = apply_parameters(base, comps, [], [p2], {"p": 1e-6})
    chk2 = check_deformation(base, tiny, [p2])
    assert chk2.stats["new_intersections"] == 0


def test_displacement_limit(car):
    s, comps, wheels = car
    p = _param(deformation=TranslateDef(direction=(0, 0, 1)), components=["wing"], lower=0, upper=0.1, max_displacement=0.02)
    r = apply_parameters(s, comps, wheels, [p], {"p": 0.05})
    chk = check_deformation(s, r, [p])
    assert not chk.valid and chk.errors[0]["code"] == "geometry.displacement_limit"


def test_static_edge_piercing_a_moved_face_is_found():
    # a large moved triangle (z = 0 plane) pierced by a small static triangle's edge; the moved
    # triangle's own edges miss the static one
    pts = np.array([[-5, -5, 0], [5, -5, 0], [0, 5, 0], [0, 0, -0.5], [0.2, 0, 0.5], [0, 0.2, 0.5]], dtype=float)
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    hits = edge_triangle_hits(pts, faces, np.array([0]))
    assert hits == {(0, 1)}
