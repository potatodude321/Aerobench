"""Adding bodies to imported geometry: parts from the same source, and modelled refinement volumes."""

import numpy as np
import pytest

from aerobench.core.errors import AeroBenchError
from aerobench.domain.mesh import SurfaceShape
from aerobench.foam.adapters import get_adapter
from aerobench.foam.case.builder import build_mesh_case
from aerobench.geometry.fixtures import box, generic_car, wing
from aerobench.geometry.io import write_stl
from aerobench.geometry.trisurface import TriSurface
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services.mesh import plan_mesh, refinement_bodies


def _raw_car(scale=1.0):
    """The synthetic car as a CAD export would have it: in mm, nose along +y, z up."""
    c = generic_car()
    rot = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)  # x(forward = -x in the CFD frame) -> ...
    return TriSurface(c.points @ rot.T * scale, c.faces, c.regions, list(c.region_names)), rot


@pytest.fixture()
def car(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    raw, rot = _raw_car(1000.0)
    write_stl(tmp_path / "car.stl", raw)
    p = Project.create(tmp_path / "p", "p")
    geo.import_geometry(p, tmp_path / "car.stl", units="mm", forward_axis="-y", up_axis="+z")
    return p, rot


def _part_points(p, cid):
    from aerobench.geometry.pose import component_surface

    return component_surface(p.cfd_surface(), p.config.geometry.component(cid)).points


def test_added_parts_inherit_units_axes_and_placement(tmp_path, car):
    p, rot = car
    before = {c.id: _part_points(p, c.id).copy() for c in p.config.geometry.components}
    wheels = [w.model_dump() for w in p.config.vehicle.wheels]
    # a rear wing modelled in the same CAD assembly (same mm, same axes, same origin)
    w = wing(0.25, 1.4, (4.0, 0.0, 1.3), angle_deg=8.0)
    write_stl(tmp_path / "rear_wing.stl", TriSurface(w.points @ rot.T * 1000.0, w.faces, w.regions, list(w.region_names)))
    res = geo.add_bodies(p, [tmp_path / "rear_wing.stl"], "parts", role="wing")
    (comp,) = res["components"]
    assert comp["role"] == "wing" and comp["name"] == "rear_wing"
    # nothing that was there moved
    for cid, pts in before.items():
        assert np.allclose(_part_points(p, cid), pts)
    assert [w.model_dump() for w in p.config.vehicle.wheels] == wheels
    # the wing sits where it was modelled relative to the car: same offset as in the source file
    cfd_car = generic_car()
    shift = before[p.config.geometry.components[0].id].min(0) - cfd_car.subset_regions([cfd_car.region_names[0]]).points.min(0)
    got = _part_points(p, comp["id"])
    assert got.min(0) == pytest.approx(w.points.min(0) * [1, 1, 1] + shift, abs=1e-6)
    # a second file exported in metres instead: say so, and it lands in the same place
    write_stl(tmp_path / "wing_m.stl", TriSurface(w.points @ rot.T, w.faces, w.regions, list(w.region_names)))
    res2 = geo.add_bodies(p, [tmp_path / "wing_m.stl"], "parts", units="m", role="spoiler")
    assert _part_points(p, res2["components"][0]["id"]) == pytest.approx(got, abs=1e-6)
    assert res2["components"][0]["id"] != comp["id"]


def test_wheels_cannot_be_added_as_parts(tmp_path, car):
    p, rot = car
    write_stl(tmp_path / "b.stl", box((0, 0, 0), (100, 100, 100), n=2))
    with pytest.raises(AeroBenchError):
        geo.add_bodies(p, [tmp_path / "b.stl"], "parts", role="wheel")


def test_modelled_refinement_volume_is_meshed_inside(tmp_path, car):
    p, rot = car
    # a closed volume around the rear of the car, in the same source frame (mm)
    lo, hi = np.array([3500.0, -1200.0, 0.0]), np.array([6000.0, 1200.0, 1600.0])
    b = box(lo, hi, n=4)
    write_stl(tmp_path / "wake.stl", TriSurface(b.points @ rot.T, b.faces, b.regions, list(b.region_names)))
    res = geo.add_bodies(p, [tmp_path / "wake.stl"], "refinement", level=5)
    (reg,) = res["regions"]
    assert reg["shape"]["kind"] == "surface" and reg["level"] == 5
    assert reg["shape"]["volume"] == pytest.approx(2.5 * 2.4 * 1.6, rel=1e-6)
    bodies = refinement_bodies(p, p.config)
    body = bodies[reg["id"]]
    # placed like the parts: the car's front at x = 0 in the CFD frame, the box 3.5 m behind its source origin
    car_raw_min_x = generic_car().points[:, 0].min()
    assert body.bbox()[0][0] == pytest.approx(3.5 - car_raw_min_x, abs=1e-6)
    plan, rc, _ = plan_mesh(p)
    rp = next(r for r in plan.regions if r.id == reg["id"])
    assert rp.level == 5 and rp.volume == pytest.approx(9.6, rel=1e-6)
    gc = build_mesh_case(p.config, rc, get_adapter("opencfd", "v2606", True))
    snappy = gc.render_all()["system/snappyHexMeshDict"]
    assert f"ref_{reg['id']}.stl" in snappy and "triSurfaceMesh" in snappy and f"ref_{reg['id']}" in snappy.split("refinementRegions")[1]
    # the mesh is out of date once a refinement body is added
    from aerobench.services.mesh import mesh_hash

    h = mesh_hash(p.config)
    p.update(lambda c: setattr(c.mesh.regions[-1], "level", 6))
    assert mesh_hash(p.config) != h
    assert isinstance(p.config.mesh.regions[-1].shape, SurfaceShape)


def test_open_surface_is_refused_as_a_refinement_volume(tmp_path, car):
    p, rot = car
    b = box((0, 0, 0), (1000, 1000, 1000), n=2)
    keep = np.ones(b.n_faces, bool)
    keep[:2] = False  # a hole
    write_stl(tmp_path / "open.stl", TriSurface(b.points, b.faces[keep], b.regions[keep], list(b.region_names)))
    with pytest.raises(AeroBenchError) as e:
        geo.add_bodies(p, [tmp_path / "open.stl"], "refinement")
    assert e.value.diagnostic.code == "geometry.refinement_open"


def test_overlapping_refinement_volumes_are_counted_once():
    from aerobench.meshing.plan import RegionPlan, exclusive_volumes

    def box(rid, lo, hi, level):
        vol = float(np.prod(np.subtract(hi, lo)))
        return RegionPlan(rid, rid, "user", level, 1.0 / 2**level, {"kind": "box", "min": lo, "max": hi}, False, vol)

    outer = box("outer", [0, 0, 0], [4, 2, 2], 3)  # 16 m³
    inner = box("inner", [1, 0, 0], [2, 1, 1], 5)  # 1 m³, finer, inside
    hidden = box("hidden", [1.2, 0.2, 0.2], [1.8, 0.8, 0.8], 4)  # inside the finer one: no effect
    half = box("half", [3, 0, 0], [5, 2, 2], 3)  # same level as outer, listed after it: half counted
    v = exclusive_volumes([outer, inner, hidden, half])
    assert v[0] == pytest.approx(15.0, rel=0.02) and v[1] == pytest.approx(1.0) and v[2] == pytest.approx(0.0, abs=1e-9)
    assert v[3] == pytest.approx(4.0, rel=0.05)
