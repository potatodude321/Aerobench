"""Shape zones: smooth local morphing that leaves everything outside the zone untouched."""

import numpy as np
import pytest

from aerobench.core.errors import AeroBenchError
from aerobench.domain.exploration import ShapeZone, ZoneDef
from aerobench.domain.project import stage_hashes
from aerobench.geometry.deform import apply_parameters, smootherstep, zone_displacement, zone_section, zone_window
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services import studies as ssvc
from aerobench.services import zones as zsvc
from aerobench.viz.payload import pack


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    write_stl(tmp_path / "car.stl", generic_car())
    p = Project.create(tmp_path / "p", "p")
    geo.import_geometry(p, tmp_path / "car.stl")
    return p


def _block(header, blob, name):
    off = 0
    for b in header["blocks"]:
        size = 4 * b["count"] if b["dtype"] in ("float32", "uint32", "int32") else 2 * b["count"]
        if b["name"] == name:
            return np.frombuffer(blob[off:off + size], dtype={"float32": "<f4", "uint32": "<u4", "uint16": "<u2", "int32": "<i4"}[b["dtype"]])
        off += size + (-size) % 4
    raise KeyError(name)


def test_window_and_sections():
    t = np.linspace(0, 1, 101)
    assert np.allclose(smootherstep(t) + smootherstep(1 - t), 1.0)
    lo, hi = np.zeros(3), np.ones(3)
    pts = np.array([[0.5, 0.5, 0.5], [0.0, 0.5, 0.5], [1.2, 0.5, 0.5], [0.05, 0.5, 0.5]])
    w = zone_window(pts, lo, hi, 0.2)
    assert w[0] == pytest.approx(1.0) and w[1] == 0.0 and w[2] == 0.0 and 0 < w[3] < 0.2
    # C2 at the face: the weight grows with the cube of the distance from it
    eps = 1e-3
    assert zone_window(np.array([[eps, 0.5, 0.5]]), lo, hi, 0.2)[0] == pytest.approx(10 * (eps / 0.2) ** 3, rel=0.01)
    x = np.linspace(-0.1, 1.1, 241)
    for n in (2, 3, 4):
        tot = sum(zone_section(x, 0.0, 1.0, 0.2, n, k) for k in range(n))
        assert np.allclose(tot, 1.0)
        for k in range(n):
            assert zone_section(x, 0.0, 1.0, 0.2, n, k).max() == pytest.approx(1.0)


def test_zone_moves_only_inside_and_by_its_value():
    d = ZoneDef(zone="z", box_min=(0, -1, 0), box_max=(1, 1, 1), blend=0.1, direction="z")
    pts = np.array([[0.5, 0.0, 0.5], [1.5, 0.0, 0.5], [0.5, 0.0, -0.01], [0.95, 0.0, 0.5]])
    disp = zone_displacement(pts, d, 20.0)  # mm
    assert disp[0, 2] == pytest.approx(0.020)
    assert np.all(disp[1:3] == 0.0)
    assert 0 < disp[3, 2] < 0.020
    dw = ZoneDef(zone="z", box_min=(0, -1, 0), box_max=(1, 1, 1), blend=0.1, direction="width", half_width=0.5)
    w = zone_displacement(np.array([[0.5, 0.5, 0.5], [0.5, -0.5, 0.5], [0.5, 0.0, 0.5]]), dw, 10.0)
    assert w[0, 1] == pytest.approx(0.010) and w[1, 1] == pytest.approx(-0.010) and w[2, 1] == 0.0


def _zone(proj, **kw):
    pre = {p["preset"]: p for p in zsvc.presets(proj)}
    base = dict(pre["rear_deck"])
    base.update({"id": "deck", "name": "Rear deck"})
    base.update(kw)
    return {k: v for k, v in base.items() if k != "description"}


def test_set_zones_generates_parameters_and_keeps_manual_ones(proj):
    p = proj
    manual = {"id": "wing_aoa", "name": "Wing", "lower": -1, "upper": 1, "deformation": {"kind": "translate", "direction": [0, 0, 0.01]}}
    ssvc.set_parameters(p, [manual])
    h0 = stage_hashes(p.config)
    out = zsvc.set_zones(p, [_zone(p)])
    ids = [x["id"] for x in out["parameters"]]
    assert ids == ["deck_z1", "deck_z2", "deck_x"]
    assert [x.id for x in p.config.parameters] == ["wing_aoa", "deck_z1", "deck_z2", "deck_x"]
    assert stage_hashes(p.config) == h0  # defining zones changes nothing until a design uses them
    # the symmetric zone's box is made symmetric about the centreline
    z = p.config.zones[0]
    yc = p.config.vehicle.centreline_y
    assert z.box_min[1] - yc == pytest.approx(-(z.box_max[1] - yc))
    # an edited copy of a zone parameter sent through the parameter editor is ignored
    edited = [x.model_dump(mode="json") for x in p.config.parameters]
    edited[1]["upper"] = 999
    ssvc.set_parameters(p, edited)
    assert next(x for x in p.config.parameters if x.id == "deck_z1").upper == 30
    # removing the zone removes its parameters, not the manual one
    zsvc.set_zones(p, [])
    assert [x.id for x in p.config.parameters] == ["wing_aoa"]


def test_morph_outside_zone_is_exactly_zero(proj):
    p = proj
    zsvc.set_zones(p, [_zone(p)])
    cfg = p.config
    cfd = p.cfd_surface(cfg=cfg)
    res = apply_parameters(cfd, cfg.geometry.components, cfg.vehicle.wheels, cfg.parameters, {"deck_z2": 25.0, "deck_z1": 0.0, "deck_x": 0.0},
                           cfg.vehicle.centreline_y)
    z = cfg.zones[0]
    inside = np.all((cfd.points > np.asarray(z.box_min)) & (cfd.points < np.asarray(z.box_max)), axis=1)
    mag = np.linalg.norm(res.displacement, axis=1)
    assert inside.any() and mag[~inside].max() == 0.0
    assert 0.0 < mag.max() <= 0.025 + 1e-12
    # symmetric about the centreline
    yc = cfg.vehicle.centreline_y
    moved = mag > 1e-9
    mirrored = cfd.points[moved] * [1, -1, 1] + [0, 2 * yc, 0]
    from scipy.spatial import cKDTree

    dist, j = cKDTree(cfd.points).query(mirrored)
    close = dist < 1e-9
    assert close.any()
    assert np.allclose(res.displacement[moved][close][:, [0, 2]], res.displacement[j[close]][:, [0, 2]], atol=1e-12)


def test_previews(proj):
    p = proj
    zsvc.set_zones(p, [_zone(p)])
    header, blob = zsvc.morph_preview(p, p.config, {"deck_z1": 20.0}, check=True)
    names = [b["name"] for b in header["blocks"]]
    assert {"positions", "indices", "scalars", "vectors", "part_ids"} <= set(names)
    st = header["stats"]
    assert 0 < st["moved_area_pct"] < 50 and st["max_movement_mm"] == pytest.approx(20.0, rel=0.05)
    assert header["check"]["valid"] in (True, False)
    assert len(pack(header, blob)) > 0
    vec = _block(header, blob, "vectors").reshape(-1, 3)
    assert np.linalg.norm(vec, axis=1).max() == pytest.approx(0.020, rel=0.05)
    fh, fb = zsvc.freedom_preview(p)
    sc = _block(fh, fb, "scalars")
    assert fh["scalar_units"] == "%" and sc.min() == 0.0 and sc.max() == pytest.approx(100.0)
    assert 0 < np.count_nonzero(sc) < len(sc) / 2
    # a zone with an unknown part is refused
    with pytest.raises(AeroBenchError) as e:
        zsvc.set_zones(p, [_zone(p, components=["nope"])])
    assert e.value.diagnostic.code == "zones.component"
    assert ShapeZone.model_validate(_zone(p)).moves[0].direction == "z"


def test_zone_that_misses_the_part_is_reported(proj):
    pr = {p["preset"]: p for p in zsvc.presets(proj)}["rear_deck"]
    good = {"id": "deck", "name": "Deck", "preset": "rear_deck", "box_min": pr["box_min"], "box_max": pr["box_max"], "blend": pr["blend"],
            "moves": [{"direction": "z", "max_mm": 20, "segments": 1}]}
    lo, hi = np.asarray(pr["box_min"]), np.asarray(pr["box_max"])
    away = {**good, "id": "away", "name": "Away", "box_min": (lo + [0, 0, 5]).tolist(), "box_max": (hi + [0, 0, 5]).tolist()}
    header, _ = zsvc.freedom_preview(proj, [good, away])
    cov = {c["id"]: c["area_pct"] for c in header["coverage"]}
    assert cov["deck"] > 1 and cov["away"] == 0
    defs = zsvc.parameters_with_zones(proj, [away])
    header, _ = zsvc.morph_preview(proj, proj.config, {"away_z": 20.0}, defs, check=False, posed=False)
    assert header["stats"]["max_movement_mm"] == 0 and "Nothing moves" in header["notes"][0]
    defs = zsvc.parameters_with_zones(proj, [good])
    header, _ = zsvc.morph_preview(proj, proj.config, {"deck_z": 20.0}, defs, check=False, posed=False)
    assert header["stats"]["max_movement_mm"] == pytest.approx(20.0, rel=0.01) and not header["notes"]


def test_element_zones_are_drawn_on_the_element_as_imported(tmp_path, monkeypatch):
    from aerobench.domain.project import ProjectConfig
    from aerobench.domain.templates import apply_template
    from aerobench.geometry.fixtures import wing
    from aerobench.services import element as el

    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    write_stl(tmp_path / "wing.stl", wing(0.3, 1.2, (0.0, 0.0, 0.5)))
    p = Project.create(tmp_path / "w", "w", apply_template(ProjectConfig(), "aero_element"))
    geo.import_geometry(p, tmp_path / "wing.stl")
    el.apply_element_setup(p, environment="ground_effect", ground_clearance=0.05, angle_deg=8.0)
    pres = zsvc.presets(p)
    assert {x["preset"] for x in pres} >= {"leading_edge", "trailing_edge", "camber", "tips"}
    zs = [{"id": x["preset"], "name": x["name"], "preset": x["preset"], "box_min": x["box_min"], "box_max": x["box_max"],
           "blend": x["blend"], "moves": x["moves"]} for x in pres]
    header, blob = zsvc.freedom_preview(p, zs)
    assert all(c["area_pct"] > 1 for c in header["coverage"])  # every typical zone covers part of the element
    assert header["ground_z"] is None
    # unposed: the payload's points are the element as imported (no angle, no height)
    pts = _block(header, blob, "positions").reshape(-1, 3)
    cfd = p.cfd_surface()
    assert pts.min(0) == pytest.approx(cfd.points.min(0), abs=1e-5) and pts.max(0) == pytest.approx(cfd.points.max(0), abs=1e-5)
    posed, _ = geo.preview_payload(p)
    assert posed["ground_z"] is not None
