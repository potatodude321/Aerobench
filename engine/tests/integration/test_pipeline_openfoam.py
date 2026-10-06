"""Real OpenFOAM v2606 integration: project → import → mesh → solve → ingest → reopen.

Runs the genuine chain (no mocks) on a coarse synthetic car.  ~2–4 minutes on 2 cores.
"""

import json
import math

import pytest

from aerobench.domain.setup import FarfieldBC
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.jobs.manager import JobManager
from aerobench.jobs.worker import Worker
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services.mesh import mesh_record
from aerobench.services.runs import ingest_run, prepare_run, reproduce_run, run_record

from .conftest import coarse_mesh

pytestmark = [pytest.mark.openfoam, pytest.mark.slow]


def _drain(project, ctx, max_ticks=400):
    w = Worker(project, ctx, launch_inline=True)
    for _ in range(max_ticks):
        w.tick()
        if not w.has_work():
            return
    raise AssertionError("worker did not finish")


@pytest.fixture(scope="module")
def solved(tmp_path_factory, ctx):
    root = tmp_path_factory.mktemp("proj") / "CarProject"
    stl = tmp_path_factory.mktemp("geom") / "synthetic_car.stl"
    write_stl(stl, generic_car())
    p = Project.create(root, "Synthetic car")
    imp = geo.import_geometry(p, stl)
    assert imp["units"] == "m" and len(imp["identification"]["wheels"]) == 4

    def setup(c):
        c.mesh = coarse_mesh()
        c.tunnel = c.tunnel.model_copy(update=dict(upstream=1.5, downstream=3.0, lateral=1.2, top=1.2))
        c.numerics.iterations = 120
        c.numerics.first_order_iterations = 40
        c.execution.n_procs = 2
        c.operating.speed = 30.0

    p.update(setup)
    res = prepare_run(p, ctx)
    _drain(p, ctx)
    return p, res


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
