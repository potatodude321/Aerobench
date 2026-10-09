"""Stage status (what 'out of date' means), hash re-keying on upgrade, live progress parsing."""

import json

import pytest

from aerobench.core.fs import sha256_json
from aerobench.core.ids import utc_now_iso
from aerobench.domain.project import ProjectConfig, stage_hashes, v1_mesh_stage_input, v1_solve_stage_input
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services.progress import snappy_phase, solver_timing


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    write_stl(tmp_path / "car.stl", generic_car())
    p = Project.create(tmp_path / "p", "p")
    geo.import_geometry(p, tmp_path / "car.stl")
    return p


def _mesh_row(p, status, cfg=None, hash_=None, i=0):
    cfg = cfg or p.config
    p.db.insert("mesh_revisions", {"id": f"mesh-{i}", "created_at": f"2026-10-0{i + 1}T00:00:00+00:00", "input_hash": hash_ or stage_hashes(cfg)["mesh"],
                                   "status": status, "path": f"meshes/mesh-{i}", "config_snapshot_json": cfg.to_json()})


def _run_row(p, status, cfg=None, hash_=None, i=0):
    cfg = cfg or p.config
    p.db.insert("runs", {"id": f"run-{i}", "created_at": f"2026-10-0{i + 1}T00:00:00+00:00", "label": "r", "status": status,
                         "solve_hash": hash_ or stage_hashes(cfg)["solve"], "config_snapshot_json": cfg.to_json(), "path": f"runs/run-{i}"})


def test_running_stopped_failed_are_not_setup_changes(proj):
    p = proj
    assert p.staleness()["mesh"]["state"] == "none"
    _mesh_row(p, "running")
    assert p.staleness()["mesh"]["state"] == "running" and not p.staleness()["mesh"]["stale"]
    p.db.update("mesh_revisions", "id", "mesh-0", {"status": "failed"})
    assert p.staleness()["mesh"]["state"] == "failed"
    p.db.update("mesh_revisions", "id", "mesh-0", {"status": "completed"})
    assert p.staleness()["mesh"]["state"] == "current"
    _run_row(p, "stopped")
    assert p.staleness()["solve"]["state"] == "stopped" and not p.staleness()["solve"]["stale"]
    # a real change of the settings is reported as such
    p.update(lambda c: setattr(c.numerics, "iterations", c.numerics.iterations + 100))
    st = p.staleness()
    assert st["solve"]["state"] == "stale" and st["mesh"]["state"] == "current"  # iterations do not change the mesh


def test_cosmetic_edits_do_not_invalidate(proj):
    p = proj
    h0 = stage_hashes(p.config)

    def rename(c):
        c.geometry.components[0].name = "Renamed body"
        c.geometry.components[0].suggested = not c.geometry.components[0].suggested
        c.vehicle.wheels[0].detection_confidence = 0.123
        c.vehicle.wheels[0].source = "manual"

    p.update(rename)
    h1 = stage_hashes(p.config)
    assert h1["mesh"] == h0["mesh"] and h1["solve"] == h0["solve"]
    # force reporting changes the solve (function objects) but not the mesh
    p.update(lambda c: setattr(c.geometry.components[0], "include_in_forces", False))
    h2 = stage_hashes(p.config)
    assert h2["mesh"] == h0["mesh"] and h2["solve"] != h0["solve"]


def test_new_settings_with_defaults_keep_hashes(proj):
    """A later release adding a setting (at its default) must not make everything stale."""
    cfg = proj.config
    data = cfg.to_json()
    data["numerics"].pop("stop_when_converged", None)  # as written by the previous release
    old = ProjectConfig.from_json(data)
    assert stage_hashes(old) == stage_hashes(cfg)


def test_upgrade_rekeys_only_verified_records(proj):
    p = proj
    cfg = p.config
    _mesh_row(p, "completed", hash_=sha256_json(v1_mesh_stage_input(cfg)), i=0)
    _run_row(p, "completed", hash_=sha256_json(v1_solve_stage_input(cfg)), i=0)
    _mesh_row(p, "completed", hash_="0" * 64, i=1)  # made by another generator: must stay unmatched
    p.db.set_meta("hash_scheme", 1)
    assert p.rehash_records() == 2
    st = p.staleness()
    assert st["mesh"]["current"]["id"] == "mesh-0" and st["solve"]["state"] == "current"
    assert p.db.one("SELECT input_hash FROM mesh_revisions WHERE id = 'mesh-1'")["input_hash"] == "0" * 64
    assert p.rehash_records() == 0  # idempotent


LOG = """
Time = 1

smoothSolver:  Solving for Ux, Initial residual = 1, Final residual = 0.01, No Iterations 2
ExecutionTime = 0.5 s  ClockTime = 1 s

Time = 2

ExecutionTime = 1.0 s  ClockTime = 1 s
""" + "".join(f"\nTime = {k}\n\nExecutionTime = {0.5 * k:.1f} s  ClockTime = {2 * k} s\n" for k in range(3, 40))


def test_solver_timing_rate_and_iteration():
    t = solver_timing(LOG)
    assert t["iteration"] == 39
    assert t["seconds_per_iteration"] == pytest.approx(2.0)  # clock spans >= 5 s: wall time used
    short = solver_timing("".join(f"\nTime = {k}\n\nExecutionTime = {0.5 * k:.1f} s  ClockTime = 1 s\n" for k in range(1, 6)))
    assert short["seconds_per_iteration"] == pytest.approx(0.5)  # clock too coarse: CPU time used


def test_snappy_phase_is_the_latest_banner():
    assert snappy_phase("Refinement phase\n...\nSurface refinement iteration 1\n") == "Refining near the surfaces"
    assert snappy_phase("Surface refinement iteration 3\nMorph iteration 2\n") == "Snapping to the surface"
    assert snappy_phase("") is None


def test_cancelled_meshing_cancels_waiting_run_with_reason(proj):
    """A run waiting for a mesh the user cancelled is cancelled (not 'failed') and says why."""
    from aerobench.jobs.manager import JobManager
    from aerobench.jobs.worker import Worker

    p = proj
    jm = JobManager(p)
    mesh_job = jm.create("mesh", p.root, [], {"kind": "local"}, target_kind="mesh", target_id="mesh-0")
    _mesh_row(p, "queued")
    _run_row(p, "queued")
    run_job = jm.create("solve", p.root, [], {"kind": "local"}, target_kind="run", target_id="run-0", depends_on=mesh_job)
    jm.request_stop(mesh_job, force=True)  # still queued: cancelled at once
    assert jm.row(mesh_job)["status"] == "cancelled"
    Worker(p, launch_inline=True).tick()
    assert jm.row(run_job)["status"] == "cancelled"
    run = p.db.one("SELECT status, diagnostics_json FROM runs WHERE id = 'run-0'")
    assert run["status"] == "cancelled"
    assert "cancelled" in json.loads(run["diagnostics_json"])[0]["summary"]
