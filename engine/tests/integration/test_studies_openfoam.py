"""Studies on the real OpenFOAM pipeline: one mesh for a yaw sweep, side-force sign,
parameterised geometry through snappyHexMesh, and reports."""

from __future__ import annotations

import pytest

from aerobench.services import reports as reports_svc
from aerobench.services import studies as svc

from .conftest import PVPYTHON, _drain

pytestmark = [pytest.mark.openfoam, pytest.mark.slow]


def test_yaw_sweep_shares_one_mesh_and_side_force_sign(solved, ctx):
    p, _ = solved
    defn = {"id": "yaw_sweep", "name": "Yaw sweep", "kind": "one_factor", "include_baseline": True,
            "variables": [{"id": "yaw", "target": "operating.yaw_deg", "lower": 0, "upper": 4}], "levels": {"yaw": [0, 4]},
            "metrics": ["Cd", "Cs", "Cm_yaw"], "scheduling": {"concurrency": 1, "cores_per_run": 2}}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    _drain(p, ctx, max_ticks=2000)
    d = svc.detail(p, sid)
    assert d["status"] == "completed", d
    assert [x["status"] for x in d["designs"]] == ["completed", "completed"]
    runs = [p.db.one("SELECT mesh_revision_id, config_snapshot_json FROM runs WHERE id = ?", (x["run_ids"][0]["run_id"],)) for x in d["designs"]]
    assert runs[0]["mesh_revision_id"] == runs[1]["mesh_revision_id"], "a yaw sweep must reuse one mesh"
    by_yaw = {x["values"]["yaw"]: x["metrics"]["aggregate"] for x in d["designs"]}
    # ADR-0007: yaw > 0 = wind from the vehicle's right -> the side force pushes the car to its left (Cs < 0)
    assert abs(by_yaw[0.0]["Cs"]["mean"]) < 0.02
    assert by_yaw[4.0]["Cs"]["mean"] < -0.01
    rep = reports_svc.create_report(p, ctx, {"kind": "study", "study_id": sid})
    assert "Yaw sweep" in reports_svc.report_file(p, rep["id"], "report.html").read_text()


def test_parameterised_geometry_runs_through_snappy(solved, ctx):
    p, _ = solved
    svc.set_parameters(p, [{"id": "boat", "name": "Boat-tail", "units": "-", "lower": 0, "upper": 1, "baseline": 0, "components": ["body"],
                            "max_displacement": 0.08,
                            "deformation": {"kind": "ffd", "box_min": [3.4, -1.0, 0.1], "box_max": [4.6, 1.0, 1.5], "divisions": [3, 2, 2],
                                            "moves": [[[2, 1, 1], [0, 0, -0.15]]]}}])
    defn = {"id": "boat", "name": "Boat tail", "kind": "table", "include_baseline": False,
            "variables": [{"id": "boat", "target": "param.boat", "lower": 0, "upper": 1}], "table": [{"boat": 1.0}],
            "scheduling": {"concurrency": 1, "cores_per_run": 2}}
    sid = svc.start(p, ctx, "study", defn, launch_worker=False)["id"]
    _drain(p, ctx, max_ticks=2000)
    d = svc.detail(p, sid)["designs"][0]
    assert d["status"] == "completed", d.get("error")
    assert d["geometry_check"]["valid"] and 0 < d["geometry_check"]["stats"]["max_displacement"] <= 0.08
    rev = p.geometry_revision(d["geometry_revision_id"])
    assert rev["kind"] == "parameterised" and rev["parameters"]["frame"] == "cfd"
    run = p.db.one("SELECT metrics_json FROM runs WHERE id = ?", (d["run_ids"][0]["run_id"],))
    assert run["metrics_json"]


@pytest.mark.paraview
def test_run_report_with_paraview_images(solved, ctx):
    if not PVPYTHON:
        pytest.skip("ParaView not available")
    from aerobench.services.viz import VizService

    p, res = solved
    viz = VizService(ctx)
    try:
        rep = reports_svc.create_report(p, ctx, {"kind": "run", "run_ids": [res["run_id"]], "images": True}, viz=viz)
    finally:
        viz.close()
    html = reports_svc.report_file(p, rep["id"], "report.html").read_text()
    assert "data:image/png;base64," in html
    assert "Aerodynamic results" in html and "Provenance" in html and "Executed commands" in html
