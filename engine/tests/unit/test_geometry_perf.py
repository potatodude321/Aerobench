"""Fast mesh kernels: identical results to the reference numpy/brute-force versions, and the
behaviour around orientation changes that the faster paths must not alter."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from aerobench.domain.geometry import WheelPosition
from aerobench.geometry.components import identify_components
from aerobench.geometry.fastops import unique_int_rows, weld_float_rows
from aerobench.geometry.fixtures import generic_car, sphere
from aerobench.geometry.frontal_area import _basis, projected_area
from aerobench.geometry.inspect import inspect_surface, with_units
from aerobench.geometry.io import read_stl
from aerobench.geometry.trisurface import TriSurface


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_unique_int_rows_matches_numpy(seed):
    rng = np.random.default_rng(seed)
    for shape, lo, hi in (((5000, 2), 0, 300), ((4000, 3), -40, 40), ((10, 3), 0, 2**40)):
        rows = rng.integers(lo, hi, shape)
        ref = np.unique(rows, axis=0, return_index=True, return_inverse=True, return_counts=True)
        got = unique_int_rows(rows, return_index=True, return_inverse=True, return_counts=True)
        for a, b in zip(ref, got):
            assert np.array_equal(np.asarray(a).reshape(-1), np.asarray(b).reshape(-1))
    assert np.array_equal(unique_int_rows(np.array([[3, 1], [1, 2], [3, 1]])), [[1, 2], [3, 1]])


def test_weld_float_rows_matches_numpy_including_signed_zero():
    rng = np.random.default_rng(5)
    pts = rng.random((3000, 3))
    pts[::5, 2] = 0.0
    pts[1::5, 2] = -0.0
    flat = pts[rng.integers(0, len(pts), 20000)]
    u1, i1 = np.unique(flat, axis=0, return_inverse=True)
    u2, i2 = weld_float_rows(flat)
    assert np.array_equal(u1, u2) and np.array_equal(i1.reshape(-1), i2)
    assert np.array_equal(u2[i2], flat)


def _reference_mask(surf, direction, res):
    """Brute-force pixel-centre-in-triangle test (the definition the rasteriser implements)."""
    pa = projected_area(surf, direction, resolution=res)
    u, v = _basis(direction)
    P = np.stack([surf.triangles() @ u, surf.triangles() @ v], axis=-1)
    h = pa.pixel_size
    nx, ny = pa.mask.shape
    xc = pa.origin[0] + (np.arange(nx) + 0.5) * h
    yc = pa.origin[1] + (np.arange(ny) + 0.5) * h
    X, Y = np.meshgrid(xc, yc, indexing="ij")
    ref = np.zeros_like(pa.mask)
    for a, b, c in P:
        def ef(p, q):
            return (q[0] - p[0]) * (Y - p[1]) - (q[1] - p[1]) * (X - p[0])
        e0, e1, e2 = ef(a, b), ef(b, c), ef(c, a)
        ref |= ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
    return pa.mask, ref


def test_scanline_rasteriser_matches_pixel_centre_definition():
    s = sphere(0.5, n_lat=12, n_lon=24)
    for d in ((1, 0, 0), (0.3, 0.2, 0.9)):
        mask, ref = _reference_mask(s, d, 80)
        # pixel centres exactly on an edge may go either way: allow a sliver
        assert np.count_nonzero(mask != ref) <= 0.002 * ref.size
        assert mask.sum() == pytest.approx(ref.sum(), rel=0.01)


def test_ascii_stl_reader_reports_bad_coordinates(tmp_path):
    p = tmp_path / "bad.stl"
    p.write_text("solid x\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 1 0 0\n   vertex 0 1 nope\n"
                 "  endloop\n endfacet\nendsolid x\n")
    with pytest.raises(Exception, match="nope"):
        read_stl(p)


def test_inspect_with_units_equals_fresh_inspection():
    car = generic_car().transformed(np.diag([1000.0, 1000.0, 1000.0, 1.0]))  # exported in millimetres
    a = with_units(inspect_surface(car, units_assumed="m"), "mm").as_dict()
    b = inspect_surface(car, units_assumed="mm").as_dict()
    assert a == b
    c = with_units(inspect_surface(car, units_assumed="mm"), "m").as_dict()
    assert c == inspect_surface(car, units_assumed="m").as_dict()
    assert any(d["code"] == "geometry.units_suspicious" for d in c["diagnostics"])


def test_reversed_nose_follows_geometry_and_says_so():
    car = generic_car()
    rot = np.diag([-1.0, -1.0, 1.0, 1.0])  # nose now at +x: 180 deg about z
    flipped = car.transformed(rot).translated((4.5, 0.0, 0.0))
    res = identify_components(flipped)
    by_pos = {w.position: w for w in res.wheels}
    assert set(by_pos) == {WheelPosition.FL, WheelPosition.FR, WheelPosition.RL, WheelPosition.RR}
    # front wheels are the ones at small x, whatever the regions were called
    assert by_pos[WheelPosition.FL].centre[0] < by_pos[WheelPosition.RL].centre[0]
    assert any("named as the" in n for n in res.notes)
    # a consistent geometry produces no such notes
    assert not any("named as the" in n for n in identify_components(car).notes)


def test_orientation_change_never_produces_negative_reference_length(tmp_path):
    import os

    os.environ.setdefault("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    from aerobench.geometry.io import write_stl
    from aerobench.project.store import Project
    from aerobench.services import geometry as geo

    write_stl(tmp_path / "car.stl", generic_car())
    p = Project.create(tmp_path / "p", "p")
    geo.import_geometry(p, tmp_path / "car.stl")
    for fwd in ("+x", "-x", "+y", "-y"):
        out = geo.set_orientation(p, fwd, "+z")
        assert p.config.reference.length > 0
        if out["wheels"]:
            assert out["wheelbase"] is None or out["wheelbase"] > 0
