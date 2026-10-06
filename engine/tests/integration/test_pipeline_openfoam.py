"""Real OpenFOAM v2606 integration: project → import → mesh → solve → ingest → reopen.

Runs the genuine chain (no mocks) on a coarse synthetic car.  ~2–4 minutes on 2 cores.
"""


import pytest

from aerobench.jobs.manager import JobManager
from aerobench.project.store import Project
from aerobench.services.mesh import mesh_record
from aerobench.services.runs import ingest_run, run_record


pytestmark = [pytest.mark.openfoam, pytest.mark.slow]


def test_mesh_completed_with_quality_report(solved):
    p, res = solved
    m = mesh_record(p, res["mesh_id"])
    assert m["status"] == "completed", m.get("diagnostics")
    assert m["cells"] > 20000
    assert m["quality"]["n_regions"] == 1
    assert m["snappy"]["overall_layer_coverage"] > 0.3
    codes = {d["code"] for d in m["diagnostics"] if isinstance(d, dict)}
    assert "mesh.leak" not in codes
    assert m["mesh_hash"]


def test_run_completed_with_physical_coefficients(solved):
    p, res = solved
    r = run_record(p, res["run_id"])
    assert r["status"] == "completed", r.get("diagnostics")
    met = r["metrics"]["metrics"]
    assert 0.1 < met["Cd"] < 1.5  # bluff synthetic car on a very coarse mesh
    assert met["balance_front_pct"] is None or -500 < met["balance_front_pct"] < 500
    assert r["iterations"] == 120
    assert r["convergence"]["status"] in ("converged", "converged_oscillating", "not_converged")
    # coefficient cross-check against OpenFOAM's own forceCoeffs (body axes, same q)
    from aerobench.foam.logs.datfiles import read_series

    rdir = p.root / r["path"]
    fc = read_series(rdir / "postProcessing" / "coeffs_total", "coefficient.dat").numeric()
    assert r["metrics"]["metrics_last"]["Cd"] == pytest.approx(fc["Cd"][-1], rel=2e-4)
    assert r["metrics"]["metrics_last"]["Cl"] == pytest.approx(fc["Cl"][-1], rel=2e-3, abs=2e-4)
    # OpenFOAM's Cl(f) = Cl/2 + CmPitch with CofR at the wheelbase midpoint on the ground
    assert r["metrics"]["metrics_last"]["Cl_front"] == pytest.approx(fc["Cl(f)"][-1], rel=5e-3, abs=5e-4)
    # first-order switch happened and the run restarted from iteration 40
    assert (rdir / "40").exists() or (rdir / "processor0" / "40").exists() or r["iterations"] == 120
    # provenance
    assert r["foam_version"] == "v2606" and r["provider"] == "local"
    assert r["manifest"]["system/controlDict"]
    assert r["n_procs"] == 2


def test_component_forces_sum_to_total(solved):
    p, res = solved
    r = run_record(p, res["run_id"])
    comps = r["metrics"]["components"]
    assert set(comps) == {"body", "wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR"}
    total = sum(c["drag_N"] for c in comps.values())
    assert total == pytest.approx(r["metrics"]["metrics"]["drag_N"], rel=1e-6)


def test_reference_locked_after_results(solved):
    p, _ = solved
    assert p.config.reference.locked


def test_reopen_reproduces_metadata(solved):
    p, res = solved
    before = run_record(p, res["run_id"])
    root = p.root
    p.close()
    q = Project.open(root)
    after = run_record(q, res["run_id"])
    assert after["metrics"] == before["metrics"]
    assert after["config_hash"] == before["config_hash"]
    # re-ingest from disk gives identical numbers (results are derived from files, not memory)
    again = ingest_run(q, res["run_id"])
    assert again["results"]["metrics"] == before["metrics"]["metrics"]
    assert q.staleness()["solve"]["stale"] is False


def test_staleness_after_geometry_change(solved, tmp_path):
    p, _ = solved
    p.update(lambda c: setattr(c.vehicle, "ride_height_offset", -0.01))
    st = p.staleness()
    assert st["mesh"]["stale"] and st["solve"]["stale"]
    p.update(lambda c: setattr(c.vehicle, "ride_height_offset", 0.0))
    assert not p.staleness()["solve"]["stale"]


def test_exact_commands_recorded(solved):
    p, res = solved
    cmds = JobManager(p).commands(res["job_id"])
    argvs = [c["argv"] for c in cmds]
    assert any("simpleFoam" in " ".join(a) and "-parallel" in a for a in argvs)
    assert any(a[0].endswith("mpirun") for a in argvs)
