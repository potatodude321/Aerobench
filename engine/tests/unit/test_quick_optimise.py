"""One-click optimisation goals: what they vary, what they protect, how much they run."""

import json

import pytest

from aerobench.core.errors import AeroBenchError
from aerobench.core.ids import utc_now_iso
from aerobench.domain.project import ProjectConfig
from aerobench.domain.templates import apply_template
from aerobench.geometry.fixtures import generic_car, wing
from aerobench.geometry.io import write_stl
from aerobench.project.store import Project
from aerobench.services import element as el
from aerobench.services import geometry as geo
from aerobench.services import quick_optimise as q
from aerobench.services import zones as zsvc
from aerobench.services.context import AppContext
from aerobench.settings import AppSettings
from aerobench.studies.design import build_design


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    return AppContext(AppSettings())


@pytest.fixture()
def car(tmp_path, ctx):
    write_stl(tmp_path / "car.stl", generic_car())
    p = Project.create(tmp_path / "car", "car")
    geo.import_geometry(p, tmp_path / "car.stl")
    p.update(lambda c: setattr(c.execution, "n_procs", 1))
    return p


@pytest.fixture()
def element(tmp_path, ctx):
    write_stl(tmp_path / "wing.stl", wing(0.3, 1.2, (0.0, 0.0, 0.5)))
    p = Project.create(tmp_path / "wing", "wing", apply_template(ProjectConfig(), "aero_element"))
    geo.import_geometry(p, tmp_path / "wing.stl")
    el.apply_element_setup(p, environment="ground_effect", ground_clearance=0.06, angle_deg=4.0)
    p.update(lambda c: setattr(c.execution, "n_procs", 1))
    return p


def fake_run(p: Project, metrics: dict, wall: float = 600.0) -> None:
    """A finished run with the given force metrics (as the results ingestion stores them)."""
    rid = f"run-{len(p.db.all('SELECT id FROM runs'))}"
    p.db.insert("runs", {"id": rid, "path": f"runs/{rid}", "created_at": utc_now_iso(), "status": "completed",
                         "config_snapshot_json": json.dumps(p.config.to_json()), "metrics_json": json.dumps({"metrics": metrics}),
                         "wall_seconds": wall, "n_procs": 1})


def test_vehicle_without_zones_gets_goal_zones(car, ctx):
    pl = q.plan(car, ctx, "drag")
    assert pl.available and {z["preset"] for z in pl.zones_to_add} == {"boat_tail", "roof_tail", "diffuser"}
    d = pl.definition
    assert [o.metric for o in d.objectives] == ["Cd"] and d.objectives[0].sense.value == "minimize"
    assert [(c.metric, c.op, c.value, c.bound.value) for c in d.constraints] == [("Cl", "<=", 0.02, "baseline_delta")]
    assert len(d.variables) == 3 and all(v.target.startswith("param.") for v in d.variables)
    # 3 variables: 7 space-filling designs + 6 guided + the baseline
    assert d.budget == 14 and d.initial_samples == 7 and d.evaluate_baseline and d.algorithm.value == "gp_bo"
    assert pl.as_dict()["estimate_seconds"] is None  # nothing has run yet
    # starting stores the zones, then the optimisation over their parameters
    detail = q.start(car, ctx, "drag", launch_worker=False)
    assert {z.preset for z in car.config.zones} == {"boat_tail", "roof_tail", "diffuser"}
    params = {p.id for p in car.config.parameters}
    assert {v["target"][6:] for v in detail["definition"]["variables"]} <= params
    assert detail["status"] == "running" and detail["definition"]["name"] == "Lower drag"
    # the next goal reuses the zones now in place instead of adding more
    pl2 = q.plan(car, ctx, "downforce")
    assert not pl2.zones_to_add and "Uses your shape zones." in pl2.notes


def test_enabled_zones_are_used_as_they_are(car, ctx):
    pr = {p["preset"]: p for p in zsvc.presets(car)}["rear_deck"]
    zsvc.set_zones(car, [{"id": "deck", "name": "Deck", "preset": "rear_deck", "box_min": pr["box_min"], "box_max": pr["box_max"],
                          "blend": pr["blend"], "moves": [{"direction": "z", "max_mm": 15, "segments": 2}]}])
    pl = q.plan(car, ctx, "downforce")
    assert not pl.zones_to_add
    assert [(v.target, v.lower, v.upper) for v in pl.definition.variables] == [("param.deck_z1", -15, 15), ("param.deck_z2", -15, 15)]
    assert [(c.metric, c.value, c.bound.value) for c in pl.definition.constraints] == [("Cd", 1.05, "baseline_ratio")]


def test_downforce_keeps_the_balance_of_a_car_that_makes_downforce(car, ctx):
    fake_run(car, {"Cd": 0.35, "Cl": -0.4, "downforce_N": 800.0, "balance_front_pct": 42.0}, wall=900)
    pl = q.plan(car, ctx, "downforce")
    cons = {(c.metric, c.op, c.value) for c in pl.definition.constraints}
    assert ("balance_front_pct", ">=", -2.0) in cons and ("balance_front_pct", "<=", 2.0) in cons
    # designs run in waves of as many as the cores allow, each as long as the last run
    assert pl.as_dict()["estimate_seconds"] == pytest.approx(900 * -(-pl.designs // pl.concurrent))
    assert pl.definition.batch_size == pl.concurrent == pl.definition.scheduling.concurrency
    assert q.plan(car, ctx, "efficiency").available


def test_efficiency_needs_downforce(car, ctx):
    fake_run(car, {"Cd": 0.3, "Cl": 0.25, "downforce_N": -300.0, "balance_front_pct": None})
    pl = q.plan(car, ctx, "efficiency")
    assert not pl.available and "lift" in pl.reason
    with pytest.raises(AeroBenchError):
        q.start(car, ctx, "efficiency", launch_worker=False)
    # and the downforce goal does not constrain an undefined balance
    assert {c.metric for c in q.plan(car, ctx, "downforce").definition.constraints} == {"Cd"}


def test_element_varies_angle_and_height(element, ctx):
    pl = q.plan(element, ctx, "downforce")
    vs = {v.target: (v.lower, v.upper, v.baseline) for v in pl.definition.variables}
    assert vs == {"element.angle_deg": (-1.0, 9.0, 4.0), "element.ground_clearance": (0.03, 0.12, 0.06)}
    assert [(c.metric, c.value) for c in pl.definition.constraints] == [("Cd", 1.10)]
    assert q.plan(element, ctx, "efficiency").definition.objectives[0].metric == "efficiency"
    # in free stream only the angle changes
    el.apply_element_setup(element, environment="free_stream")
    assert [v.target for v in q.plan(element, ctx, "drag").definition.variables] == ["element.angle_deg"]


def test_element_designs_keep_the_requested_clearance(element):
    from aerobench.geometry.pose import posed_components

    for angle, height in ((8.0, 0.04), (0.0, 0.1)):
        b = build_design(element, element.config, {"element.angle_deg": angle, "element.ground_clearance": height})
        assert b.ok, b.errors
        c = b.cfg
        parts, ground, _ = posed_components(element.cfd_surface(cfg=c), c.geometry.components, c.vehicle, c.tunnel.contact_patch_fraction)
        assert min(float(s.bbox()[0][2]) for s in parts.values() if s.n_faces) - ground == pytest.approx(height, abs=1e-6)
        assert c.vehicle.rake_deg == pytest.approx(angle)


def test_unknown_goal_is_refused(car, ctx):
    with pytest.raises(AeroBenchError):
        q.plan(car, ctx, "speed")


def test_a_running_goal_is_not_started_twice(element, ctx):
    d = q.start(element, ctx, "downforce", launch_worker=False)
    goals = {g["goal"]: g for g in q.plans(element, ctx)}
    assert goals["downforce"]["active_study_id"] == d["id"] and goals["drag"]["busy"] and not goals["downforce"]["busy"]
    with pytest.raises(AeroBenchError) as e:
        q.start(element, ctx, "downforce", launch_worker=False)
    assert e.value.diagnostic.code == "optimise.already_running"
