"""Study/optimisation driver against a fake solver (no OpenFOAM): design generation,
bounded retries, failures as data, baseline-relative constraints, cancellation,
optimiser checkpointing through the database, geometry-parameter designs and reports."""

from __future__ import annotations

import json

import pytest

import aerobench.services.runs as runs_mod
from aerobench.core.errors import AeroBenchError
from aerobench.core.ids import short_id, utc_now_iso
from aerobench.domain.project import ProjectConfig
from aerobench.domain.templates import apply_template
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.project.db import dec
from aerobench.project.store import Project
from aerobench.services import reports as reports_svc
from aerobench.services import studies as svc
from aerobench.services.context import AppContext
from aerobench.services.geometry import import_geometry
from aerobench.settings import AppSettings
from aerobench.studies.design import build_design
from aerobench.studies.driver import drive_all


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    p = Project.create(tmp_path / "proj", "Study test", apply_template(ProjectConfig(), "generic_road_car"))
    stl = tmp_path / "car.stl"
    write_stl(stl, generic_car(rear_wing=True))
    import_geometry(p, stl)
    ctx = AppContext(AppSettings(max_cores=8))
    calls: list[dict] = []

    def fake_prepare_run(project, ctx_, cfg, label=None, study_id=None, design_id=None, **kw):
        rid = short_id("run")
        project.db.insert("runs", {
            "id": rid, "created_at": utc_now_iso(), "label": label, "status": "queued", "job_id": None, "mesh_revision_id": None,
            "geometry_revision_id": cfg.geometry.revision_id, "config_hash": "x", "solve_hash": "x", "config_snapshot_json": cfg.to_json(),
            "operating_json": cfg.operating.model_dump(mode="json"), "reference_json": cfg.reference.model_dump(mode="json"),
            "provider": "fake", "n_procs": cfg.execution.n_procs, "study_id": study_id, "design_id": design_id, "path": f"runs/{rid}",
        })
        calls.append({"run_id": rid, "cfg": cfg, "design_id": design_id})
        return {"run_id": rid, "job_id": None}

    monkeypatch.setattr(runs_mod, "prepare_run", fake_prepare_run)
    return p, ctx, calls


def physics(cfg: ProjectConfig) -> dict:
    """Smooth analytic stand-in for the solver."""
    yaw = cfg.operating.yaw_deg
    rh = cfg.vehicle.ride_height_offset
    wing = cfg.parameter_values.get("wing_h", 0.0)
    cd = 0.30 + 0.0008 * yaw**2 + 0.4 * (rh - 0.01) ** 2 + 0.2 * wing**2
    cl = -0.10 + 2.0 * rh - 0.5 * wing + 0.002 * abs(yaw)
    return {"Cd": cd, "Cl": cl, "Cl_front": cl * 0.4, "Cl_rear": cl * 0.6, "balance_front_pct": 40.0, "drag_N": cd * 963 * 2.2,
            "downforce_N": -cl * 963 * 2.2}


def solve_all(p: Project, fail=lambda cfg: False, status="completed", conv="converged") -> int:
    n = 0
    for r in p.db.all("SELECT id, config_snapshot_json FROM runs WHERE status = 'queued'"):
        cfg = ProjectConfig.model_validate(json.loads(r["config_snapshot_json"]))
        if fail(cfg):
            p.db.update("runs", "id", r["id"], {"status": "failed", "diagnostics_json": [{"code": "solver.crash", "summary": "boom"}]})
        else:
            p.db.update("runs", "id", r["id"], {"status": status, "metrics_json": {"metrics": physics(cfg)}, "convergence_json": {"status": conv}})
        n += 1
    return n


def drive(p, ctx, rounds=200, **solve_kw):
    for _ in range(rounds):
        drive_all(p, ctx)
        solved = solve_all(p, **solve_kw)
        st = [r["status"] for r in p.db.all("SELECT status FROM studies")]
        if all(s in ("completed", "failed", "cancelled") for s in st) and not solved:
            break


def test_one_factor_yaw_study(env):
    p, ctx, calls = env
    defn = {"id": "yaw", "name": "Yaw sweep", "kind": "one_factor", "include_baseline": True,
            "variables": [{"id": "yaw", "target": "operating.yaw_deg", "lower": -10, "upper": 10}],
            "levels": {"yaw": [-10, -5, 0, 5, 10]}, "scheduling": {"concurrency": 2}}
    pre = svc.preview(p, ctx, "study", defn)
    assert not pre["errors"] and len(pre["designs"]) == 5  # baseline (yaw 0) is not duplicated
    st = svc.start(p, ctx, "study", defn, launch_worker=False)
    sid = st["id"]
    drive_all(p, ctx)
    assert sum(1 for d in svc.detail(p, sid)["designs"] if d["status"] == "running") == 2  # concurrency respected
    drive(p, ctx)
    d = svc.detail(p, sid)
    assert d["status"] == "completed"
    assert all(x["status"] == "completed" for x in d["designs"])
    # one mesh for all yaw angles: the tunnel is sized for the full yaw range
    assert {tuple(c["cfg"].tunnel.design_yaw_range_deg) for c in calls} == {(-10.0, 10.0)}
    cds = {x["values"]["yaw"]: x["metrics"]["aggregate"]["Cd"]["mean"] for x in d["designs"]}
    assert cds[10.0] == pytest.approx(0.30 + 0.08 + 0.4 * 0.01**2)
    csv = svc.export_csv(p, sid)
    assert csv.splitlines()[0].startswith("design,idx,origin,status") and len(csv.splitlines()) == 6


def test_failures_are_data_and_retries_are_bounded(env):
    p, ctx, calls = env
    defn = {"id": "rh", "name": "Ride height", "kind": "full_factorial", "include_baseline": False,
            "variables": [{"id": "rh", "target": "vehicle.ride_height_offset", "lower": -0.01, "upper": 0.02}],
            "levels": {"rh": [-0.01, 0.0, 0.02]}, "scheduling": {"concurrency": 3, "max_retries": 2}}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    drive(p, ctx, fail=lambda cfg: cfg.vehicle.ride_height_offset > 0.015)
    d = svc.detail(p, sid)
    assert d["status"] == "completed"
    bad = next(x for x in d["designs"] if x["values"]["rh"] == 0.02)
    assert bad["status"] == "failed" and bad["error"]["code"] == "solver.crash"
    assert bad["attempts"] == 1  # a solver crash is not retried blindly (not a transient failure)
    assert sum(1 for x in d["designs"] if x["status"] == "completed") == 2


def test_divergence_is_retried_with_robust_numerics(env):
    p, ctx, calls = env
    defn = {"id": "s", "name": "S", "kind": "table", "include_baseline": False,
            "variables": [{"id": "yaw", "target": "operating.yaw_deg", "lower": 0, "upper": 5}], "table": [{"yaw": 5}],
            "scheduling": {"max_retries": 1}}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    drive_all(p, ctx)
    r = p.db.one("SELECT id FROM runs")
    p.db.update("runs", "id", r["id"], {"status": "failed", "diagnostics_json": [{"code": "solver.divergence", "summary": "diverged"}]})
    drive(p, ctx)
    d = svc.detail(p, sid)["designs"][0]
    assert d["status"] == "completed" and d["attempts"] == 2
    assert calls[-1]["cfg"].numerics.preset.value == "robust"


def test_gp_bo_with_baseline_relative_constraint(env):
    p, ctx, calls = env
    defn = {"id": "opt", "name": "Min drag", "algorithm": "gp_bo", "budget": 14, "seed": 1,
            "variables": [{"id": "rh", "target": "vehicle.ride_height_offset", "lower": -0.02, "upper": 0.03},
                          {"id": "yaw", "target": "operating.yaw_deg", "lower": -4, "upper": 4}],
            "objectives": [{"metric": "Cd", "sense": "minimize"}],
            # downforce must not drop by more than 0.02 in Cl relative to the baseline
            "constraints": [{"metric": "Cl", "op": "<=", "value": 0.02, "bound": "baseline_delta"}],
            "scheduling": {"concurrency": 2}}
    sid = svc.start(p, ctx, "optimisation", defn, launch_worker=False)["id"]
    drive_all(p, ctx)
    # baseline-relative constraint: only the baseline runs until it is known
    assert [x["origin"] for x in svc.detail(p, sid)["designs"]] == ["baseline"]
    drive(p, ctx, fail=lambda cfg: cfg.operating.yaw_deg > 3.5)
    d = svc.detail(p, sid)
    assert d["status"] == "completed"
    assert len(d["designs"]) <= 14
    st = dec(p.db.one("SELECT state_json FROM optimiser_state WHERE study_id = ?", (sid,)))["state"]
    assert len(st["observations"]) + len(st["pending"]) == len([x for x in d["designs"] if x.get("token")])
    best = next(x for x in d["designs"] if x["id"] == d["summary"]["best_design_id"])
    assert best["feasible"] and best["objectives"][0]["value"] <= 0.30 + 0.4 * 0.01**2 + 1e-9  # no worse than the baseline
    base_cl = next(x for x in d["designs"] if x["origin"] == "baseline")["metrics"]["aggregate"]["Cl"]["mean"]
    assert best["metrics"]["aggregate"]["Cl"]["mean"] <= base_cl + 0.02 + 1e-9
    assert d["history"] and d["history"][-1]["best"] == pytest.approx(d["summary"]["best_value"])


def test_multi_condition_nsga2_and_cancel(env):
    p, ctx, calls = env
    defn = {"id": "mo", "name": "Pareto", "algorithm": "nsga2", "budget": 40, "seed": 0,
            "variables": [{"id": "rh", "target": "vehicle.ride_height_offset", "lower": -0.02, "upper": 0.03}],
            "objectives": [{"metric": "Cd", "sense": "minimize", "aggregate": "mean"}, {"metric": "Cl", "sense": "minimize", "aggregate": "mean"}],
            "conditions": [{"operating.yaw_deg": 0}, {"operating.yaw_deg": 5}], "evaluate_baseline": False,
            "scheduling": {"concurrency": 4}}
    sid = svc.start(p, ctx, "optimisation", defn, launch_worker=False)["id"]
    drive(p, ctx, rounds=6)
    d = svc.detail(p, sid)
    done = [x for x in d["designs"] if x["status"] == "completed"]
    assert done and all(len(x["run_ids"]) == 2 for x in done)
    x = done[0]
    assert x["objectives"][0]["value"] == pytest.approx(sum(x["objectives"][0]["per_condition"]) / 2)
    with pytest.raises(AeroBenchError):
        svc.set_status(p, sid, "resume", launch_worker=False)  # not paused
    svc.set_status(p, sid, "pause", launch_worker=False)
    svc.set_status(p, sid, "cancel", launch_worker=False)
    drive_all(p, ctx)
    d = svc.detail(p, sid)
    assert d["status"] == "cancelled"
    assert not [x for x in d["designs"] if x["status"] in ("running", "pending")]


def test_invalid_definitions_are_rejected(env):
    p, ctx, _ = env
    bad = {"id": "b", "name": "B", "kind": "one_factor", "variables": [{"id": "x", "target": "nope.nothing", "lower": 0, "upper": 1}]}
    assert svc.preview(p, ctx, "study", bad)["errors"]
    over = {"id": "c", "name": "C", "kind": "one_factor", "variables": [{"id": "x", "target": "operating.speed", "lower": 30, "upper": 50}],
            "scheduling": {"concurrency": 8, "cores_per_run": 4}}
    errs = svc.preview(p, ctx, "study", over)["errors"]
    assert any("exceed" in e["message"] for e in errs)
    with pytest.raises(AeroBenchError):
        svc.start(p, ctx, "study", over, launch_worker=False)


def test_parameterised_geometry_design(env):
    p, ctx, calls = env
    svc.set_parameters(p, [{"id": "wing_h", "name": "Wing height", "units": "m", "lower": -0.6, "upper": 0.1, "baseline": 0.0,
                            "components": ["wing"], "deformation": {"kind": "translate", "direction": [0, 0, 1]}}])
    base = p.config.model_copy(deep=True)
    b1 = build_design(p, base, {"param.wing_h": 0.05})
    assert b1.ok, b1.errors
    assert b1.cfg.geometry.revision_id != base.geometry.revision_id
    assert b1.cfg.geometry.transform.auto_place is False and b1.cfg.geometry.sizing_box is not None
    b2 = build_design(p, base, {"param.wing_h": 0.05})
    assert b2.cfg.geometry.revision_id == b1.cfg.geometry.revision_id  # cached
    # the wing translated by exactly 50 mm in the CFD frame; nothing else moved
    s0 = p.cfd_surface(cfg=base)
    s1 = p.cfd_surface(cfg=b1.cfg)
    wi = s0.region_names.index("wing")
    import numpy as np

    wv = np.unique(s0.faces[s0.regions == wi])
    assert np.allclose(s1.points[wv] - s0.points[wv], [0, 0, 0.05], atol=1e-9)
    other = np.setdiff1d(np.arange(s0.n_points), wv)
    assert np.allclose(s1.points[other], s0.points[other], atol=1e-9)
    # pushing the wing into the body is an invalid design, reported with the reason
    b3 = build_design(p, base, {"param.wing_h": -0.6})
    assert not b3.ok and b3.errors[0]["code"] in ("geometry.self_intersection", "geometry.ground_contact", "geometry.enclosed")
    # baseline value: identical geometry, no new revision
    b4 = build_design(p, base, {"param.wing_h": 0.0})
    assert b4.cfg.geometry.revision_id == base.geometry.revision_id and b4.cfg.geometry.sizing_box is None


def test_study_report_is_self_contained(env):
    p, ctx, _ = env
    defn = {"id": "yaw", "name": "Yaw", "kind": "one_factor", "variables": [{"id": "yaw", "target": "operating.yaw_deg", "lower": 0, "upper": 6}],
            "levels": {"yaw": [0, 3, 6]}}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    drive(p, ctx)
    rep = reports_svc.create_report(p, ctx, {"kind": "study", "study_id": sid})
    html = reports_svc.report_file(p, rep["id"], "report.html").read_text()
    assert "<svg" in html and "Design exploration" in html and "http" not in html.split("<body>")[1].replace("http://www.w3.org", "")
    csv = reports_svc.report_file(p, rep["id"], "data.csv").read_text()
    assert len(csv.splitlines()) == 4


def test_mesh_sensitivity_study_reports_gci(env, monkeypatch):
    p, ctx, calls = env
    import tests.unit.test_studies as me

    def mesh_physics(cfg):
        h = cfg.mesh.base_cell_size
        return {"Cd": 0.30 + 2.0 * h**2, "Cl": -0.1 + h, "Cl_front": 0, "Cl_rear": 0, "balance_front_pct": 40.0, "drag_N": 1, "downforce_N": 1}

    monkeypatch.setattr(me, "physics", mesh_physics)
    defn = {"id": "mesh", "name": "Mesh sensitivity", "kind": "one_factor", "include_baseline": False,
            "variables": [{"id": "h", "target": "mesh.base_cell_size", "lower": 0.1, "upper": 0.5}], "levels": {"h": [0.4, 0.2, 0.1]},
            "metrics": ["Cd", "Cl"]}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    drive(p, ctx)
    g = svc.detail(p, sid)["summary"]["gci"]["results"]
    assert g["Cd"]["status"] == "monotonic" and g["Cd"]["order"] == pytest.approx(2.0, abs=1e-6)
    assert g["Cd"]["extrapolated"] == pytest.approx(0.30, abs=1e-9)
