import math

import numpy as np
import pytest

from aerobench.domain.geometry import AxisDir, ComponentRole, GeometryTransform, WheelPosition
from aerobench.domain.vehicle import VehicleConfig, Wheel
from aerobench.geometry import repair
from aerobench.geometry.components import identify_components, role_from_name, wheel_position_from_name
from aerobench.geometry.fixtures import AHMED, ahmed_body, box, cylinder, generic_car, sphere
from aerobench.geometry.frontal_area import projected_area
from aerobench.geometry.inspect import inspect_surface, suggest_units
from aerobench.geometry.io import read_obj, read_ply, read_stl, write_obj, write_stl
from aerobench.geometry.pose import front_rear_to_offset_rake, place_in_cfd_frame, posed_components, write_case_surfaces
from aerobench.geometry.trisurface import TriSurface


def test_stl_ascii_multisolid_and_binary_roundtrip(tmp_path):
    car = generic_car()
    p = tmp_path / "car.stl"
    write_stl(p, car)
    back = read_stl(p)
    assert back.region_names == car.region_names
    assert back.n_faces == car.n_faces
    assert inspect_surface(back).closed
    pb = tmp_path / "car_bin.stl"
    write_stl(pb, car, binary=True)
    bb = read_stl(pb)
    assert bb.n_faces == car.n_faces and len(bb.region_names) == 1
    assert np.allclose(bb.bbox()[0], car.bbox()[0], atol=1e-6)


def test_binary_stl_whose_header_starts_with_solid(tmp_path):
    s = box((0, 0, 0), (1, 1, 1), n=2)
    p = tmp_path / "b.stl"
    write_stl(p, s, binary=True, solid_name="solid trick header")
    assert read_stl(p).n_faces == s.n_faces


def test_obj_and_ply(tmp_path):
    car = generic_car()
    p = tmp_path / "car.obj"
    write_obj(p, car)
    back = read_obj(p)
    assert back.region_names == car.region_names and back.n_faces == car.n_faces
    ply = tmp_path / "t.ply"
    ply.write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
                   "element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n1 1 0\n0 1 0\n4 0 1 2 3\n")
    s = read_ply(ply)
    assert s.n_faces == 2


def test_corrupt_file_gives_structured_error(tmp_path):
    from aerobench.core.errors import AeroBenchError

    p = tmp_path / "bad.stl"
    p.write_text("solid x\n facet normal 0 0 1\n outer loop\n vertex 0 0 0\n vertex 1 0 0\n endloop\nendsolid x\n")
    with pytest.raises(AeroBenchError) as e:
        read_stl(p)
    assert e.value.diagnostic.category.value == "geometry"


def test_units_detection():
    sug = suggest_units(4500.0)
    assert sug[0].unit == "mm"
    assert suggest_units(4.5)[0].unit == "m"
    rep = inspect_surface(generic_car().transformed(np.diag([1000, 1000, 1000, 1.0])))
    assert any(d.code == "geometry.units_suspicious" for d in rep.diagnostics)


def test_diagnostics_detect_holes_flips_and_islands():
    s = sphere(n_lat=16, n_lon=32)
    # punch a hole: drop 3 faces; flip some faces; add a far tiny island
    s2 = s.subset_faces(np.arange(s.n_faces) >= 3, compact=False)
    s2.faces[10:20] = s2.faces[10:20, ::-1]
    island = box((5, 5, 5), (5.001, 5.001, 5.001), n=1)
    s3 = TriSurface.merge([s2, island])
    rep = inspect_surface(s3)
    codes = {d.code for d in rep.diagnostics}
    assert "geometry.open" in codes
    assert "geometry.orientation" in codes
    assert "geometry.tiny_islands" in codes
    fixed, records = repair.run_pipeline(s3)
    rep2 = inspect_surface(fixed)
    assert rep2.inconsistent_orientation_edges == 0
    assert rep2.boundary_edges == 0
    assert len(rep2.components) == 1
    assert rep2.outward_normals is True
    assert [r.op for r in records][0] == "merge_vertices"
    # original untouched (nondestructive)
    assert inspect_surface(s3).boundary_edges > 0


def test_orient_inside_out_body():
    s = sphere(n_lat=12, n_lon=24)
    inv = TriSurface(s.points, s.faces[:, ::-1], s.regions, s.region_names)
    assert inspect_surface(inv).outward_normals is False
    fixed, rec = repair.orient_normals(inv)
    assert inspect_surface(fixed).outward_normals is True


def test_merge_vertices_and_duplicates():
    s = box((0, 0, 0), (1, 1, 1), n=2)
    tri = s.triangles().reshape(-1, 3)  # soup
    soup = TriSurface(tri, np.arange(len(tri)).reshape(-1, 3))
    assert inspect_surface(soup).boundary_edges > 0
    merged, rec = repair.merge_vertices(soup)
    assert inspect_surface(merged).closed
    dup = TriSurface(merged.points, np.vstack([merged.faces, merged.faces[:5]]))
    out, rec = repair.remove_duplicate_faces(dup)
    assert out.n_faces == merged.n_faces


def test_split_by_component_and_angle():
    car = generic_car()
    one = TriSurface(car.points, car.faces)  # single region
    split, rec = repair.split_by_component(one)
    assert len(split.region_names) == 5
    b = box((0, 0, 0), (1, 1, 1), n=3)
    split2, _ = repair.split_by_angle(b, 45)
    assert len(split2.region_names) == 6


def test_frontal_area_of_sphere_and_box():
    r = 0.5
    s = sphere(radius=r, n_lat=64, n_lon=128)
    pa = projected_area(s, (1, 0, 0), resolution=800)
    assert pa.area == pytest.approx(math.pi * r * r, rel=0.01)
    assert pa.uncertainty < 0.01 * pa.area
    b = box((0, 0, 0), (2, 1.5, 0.7), n=2)
    assert projected_area(b, (1, 0, 0)).area == pytest.approx(1.5 * 0.7, rel=0.01)
    # union semantics: two overlapping boxes count once
    two = TriSurface.merge([b, b.translated((0.5, 0, 0))])
    assert projected_area(two, (1, 0, 0)).area == pytest.approx(1.5 * 0.7, rel=0.01)


def test_ahmed_frontal_area_matches_published_value():
    body = ahmed_body(stilts=False)
    pa = projected_area(body, (1, 0, 0), resolution=1000)
    # Ahmed body frontal area 0.112 m² (rectangle minus four R=100 mm front-corner roundings
    # is NOT subtracted: the full cross-section is 0.389 x 0.288 behind the nose)
    assert pa.area == pytest.approx(0.389 * 0.288, rel=0.01)


def test_wheel_detection_on_generic_car():
    car = generic_car()
    res = identify_components(car, ground_z=0.0)
    assert len(res.wheels) == 4
    pos = {w.position for w in res.wheels}
    assert pos == {WheelPosition.FL, WheelPosition.FR, WheelPosition.RL, WheelPosition.RR}
    fl = next(w for w in res.wheels if w.position == WheelPosition.FL)
    assert fl.centre[0] == pytest.approx(0.85, abs=0.01)
    assert fl.centre[1] < 0  # left = -y
    assert fl.radius == pytest.approx(0.32, rel=0.02)
    roles = {c.role for c in res.components}
    assert ComponentRole.BODY in roles and ComponentRole.WHEEL in roles


def test_name_heuristics():
    assert role_from_name("Tyre_FrontLeft") == ComponentRole.WHEEL
    assert wheel_position_from_name("wheel_front_left") == WheelPosition.FL
    assert wheel_position_from_name("RR_wheel") == WheelPosition.RR
    assert role_from_name("rear_wing_main") == ComponentRole.WING


def test_transform_axes_and_autoplace():
    car = generic_car()
    # pretend source file uses x forward (nose at +x), y left, z up and millimetres
    m = np.diag([-1000.0, -1000.0, 1000.0, 1.0])
    src = car.transformed(m).translated((5000, 200, 300))
    t = GeometryTransform(unit_scale=0.001, forward_axis=AxisDir.PX, up_axis=AxisDir.PZ)
    cfd, t2 = place_in_cfd_frame(src, t)
    lo, hi = cfd.bbox()
    assert lo[0] == pytest.approx(0, abs=1e-9) and lo[2] == pytest.approx(0, abs=1e-9)
    assert hi[0] == pytest.approx(4.5, rel=1e-6)
    assert lo[1] == pytest.approx(-hi[1])
    assert inspect_surface(cfd).outward_normals is True
    # wheels must still be detected at the front (small x)
    res = identify_components(cfd)
    fl = next(w for w in res.wheels if w.position == WheelPosition.FL)
    assert fl.centre[0] < 2.0


def test_ride_height_and_rake(tmp_path):
    car = generic_car()
    res = identify_components(car)
    veh = VehicleConfig(wheels=res.wheels)
    parts0, z0, _ = posed_components(car, res.components, veh, 0.01)
    veh2 = veh.model_copy(update={"ride_height_offset": -0.02, "rake_deg": 1.0})
    parts1, z1, info = posed_components(car, res.components, veh2, 0.01)
    assert z0 == z1 == pytest.approx(0.0032, rel=1e-6)  # 1% of radius 0.32
    body0, body1 = parts0["body"], parts1["body"]
    # nose (small x) goes down more than tail with positive rake
    i_nose, i_tail = np.argmin(body0.points[:, 0]), np.argmax(body0.points[:, 0])
    nose_dz = body1.points[i_nose, 2] - body0.points[i_nose, 2]
    tail_dz = body1.points[i_tail, 2] - body0.points[i_tail, 2]
    # rake 1 deg about the rear axle (x=3.55): nose (x=0) drops 3.55*sin(1deg) plus the -20 mm offset
    assert nose_dz == pytest.approx(-0.02 - 3.55 * math.sin(math.radians(1.0)), abs=2e-3)
    assert nose_dz < tail_dz < 0.0
    # wheels unchanged
    assert np.allclose(parts1["wheel_FL"].points, parts0["wheel_FL"].points)
    off, rake = front_rear_to_offset_rake(-0.03, -0.01, 2.7)
    assert off == -0.01 and rake > 0
    cs = write_case_surfaces(car, res.components, veh2, 0.01, tmp_path)
    assert set(cs.files) == {c.id for c in res.components}
    assert read_stl(cs.files["body"]).n_faces == parts1["body"].n_faces
