"""Run workflow: solve case generation, job steps, ingestion, reproduce, resume."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, Diagnostic, ErrorCategory, raise_if_errors
from ..core.fs import sha256_json
from ..core.ids import short_id, utc_now_iso
from ..domain.project import ProjectConfig, solve_stage_input, validate_project
from ..foam.adapters import get_adapter
from ..foam.case.builder import assert_no_dynamic_code, build_solve_case
from ..foam.logs.solver_log import parse_solver_log
from ..jobs.manager import JobManager
from ..jobs.spec import CopyStep, DictSetStep, FoamStep, JobStatus, LinkMeshStep, RemoveStep, StageStep
from ..project.db import dec
from ..project.store import Project
from ..results.ingest import ingest_run_results
from ..version import APP_VERSION
from .context import AppContext
from .mesh import find_mesh, plan_mesh, prepare_mesh


def solve_hash(cfg: ProjectConfig) -> str:
    return sha256_json(solve_stage_input(cfg))


def solve_steps(cfg: ProjectConfig, adapter, mesh_dir: Path, nprocs: int, staged: bool, n_first: int,
                resume: bool = False) -> list:
    solver = adapter.steady_solver_argv()
    steps: list = []
    if not resume:
        steps.append(LinkMeshStep(name="link_mesh", src_case=str(mesh_dir)))
    if staged:
        steps.append(StageStep(name="stage_in", direction="in"))
    par = nprocs > 1
    if par and not resume:
        steps.append(RemoveStep(name="clean_processors", globs=["processor*"]))
        steps.append(FoamStep(name="decomposePar", app=["decomposePar", "-force"], stage="solve"))
    if cfg.numerics.potential_init and not resume:
        steps.append(FoamStep(name="potentialFoam", app=adapter.potential_init_argv(), nprocs=nprocs, optional=True, stage="solve"))
    total = cfg.numerics.iterations
    if n_first > 0 and not resume:
        # Stage 1: first-order convection; force a write at the switch so stage 2 restarts from it.
        # (Parallel runs read system/ from the case root, so editing it here applies to all ranks.)
        wi = cfg.numerics.write_interval or min(500, total)
        steps.append(CopyStep(name="schemes_first_order", src="system/fvSchemes.firstOrder", dst="system/fvSchemes"))
        steps.append(DictSetStep(name="end_first_order", file="system/controlDict", key_path="endTime", value=n_first))
        steps.append(DictSetStep(name="write_first_order", file="system/controlDict", key_path="writeInterval", value=n_first))
        steps.append(FoamStep(name="solve_first_order", app=solver, nprocs=nprocs, solver=True, stage="solve"))
        steps.append(CopyStep(name="schemes_final", src="system/fvSchemes.final", dst="system/fvSchemes"))
        steps.append(DictSetStep(name="end_final", file="system/controlDict", key_path="endTime", value=total))
        steps.append(DictSetStep(name="write_final", file="system/controlDict", key_path="writeInterval", value=wi))
    steps.append(FoamStep(name="solve", app=solver, nprocs=nprocs, solver=True, stage="solve"))
    if par:
        steps.append(FoamStep(name="reconstructPar", app=["reconstructPar", "-latestTime"], stage="solve"))
    if staged:
        steps.append(StageStep(name="stage_out", direction="out"))
    return steps


def prepare_run(
    project: Project,
    ctx: AppContext,
    cfg: Optional[ProjectConfig] = None,
    label: Optional[str] = None,
    study_id: Optional[str] = None,
    design_id: Optional[str] = None,
    reproduces: Optional[str] = None,
    launch: bool = False,
) -> dict[str, Any]:
    cfg = (cfg or project.config).model_copy(deep=True)
    raise_if_errors(validate_project(cfg, "solve"), "run configuration")
    provider, pinfo = ctx.require_provider(cfg.execution.provider_id)
    adapter = get_adapter(cfg.solver.fork.value, cfg.solver.version, ctx.settings.allow_experimental_adapters)
    n = max(1, min(cfg.execution.n_procs, ctx.max_cores()))
    if n > 1 and not pinfo.mpi.available:
        n = 1
    cfg.execution.n_procs = n
    for d in provider.check_case(project.root, n):
        raise AeroBenchError(d)
    # mesh: reuse a matching completed/pending mesh or create one
    m = find_mesh(project, cfg, ("completed", "queued", "running", "starting"))
    mesh_job = None
    if m is None:
        mres = prepare_mesh(project, ctx, cfg)
        mesh_id, mesh_job = mres["mesh_id"], mres["job_id"]
    else:
        mesh_id = m["id"]
        if m["status"] != "completed":
            mesh_job = m["job_id"]
    mrow = dec(project.db.one("SELECT * FROM mesh_revisions WHERE id = ?", (mesh_id,)))
    mesh_dir = project.root / mrow["path"]
    plan, rc, _ = plan_mesh(project, cfg)
    run_id = short_id("run")
    rdir = project.run_dir(run_id)
    rdir.mkdir(parents=True)
    gc = build_solve_case(cfg, rc, adapter)
    assert_no_dynamic_code(gc)
    n_first = gc.meta["first_order_iterations"]
    if n_first > 0:
        gc.files["system/fvSchemes.final"] = type(gc.files["system/fvSchemes"])(
            "system/fvSchemes.final", gc.files["system/fvSchemes"].body, "fvSchemes", "dictionary", "system")
    manifest = gc.write(rdir)
    (rdir / "case.foam").write_text("")  # ParaView reader stub
    steps = solve_steps(cfg, adapter, mesh_dir, n, provider.staging == "staged", n_first)
    jm = JobManager(project)
    job_id = jm.create("solve", rdir, steps, provider.serialise(), cores=n, target_kind="run", target_id=run_id, depends_on=mesh_job,
                       meta={"iterations": cfg.numerics.iterations})
    project.db.insert("runs", {
        "id": run_id, "created_at": utc_now_iso(), "label": label or f"U={cfg.operating.speed:g} m/s, yaw={cfg.operating.yaw_deg:g}°",
        "status": "queued", "job_id": job_id, "mesh_revision_id": mesh_id, "geometry_revision_id": cfg.geometry.revision_id,
        "config_hash": sha256_json(cfg.to_json()), "solve_hash": solve_hash(cfg), "config_snapshot_json": cfg.to_json(),
        "operating_json": cfg.operating.model_dump(mode="json"), "reference_json": cfg.reference.model_dump(mode="json"),
        "provider": provider.id, "foam_fork": pinfo.fork, "foam_version": pinfo.version,
        "paraview_version": (ctx.paraview() or {}).get("version"), "app_version": APP_VERSION,
        "hardware_json": ctx.hardware(), "env_digest_json": provider.environment_digest(), "manifest_json": manifest,
        "resolved_json": {**rc.summary(), "ground_z": plan.ground_z, "numerics": gc.meta.get("numerics_preset"),
                          "overrides": gc.overrides.__dict__},
        "n_procs": n, "study_id": study_id, "design_id": design_id, "path": str(rdir.relative_to(project.root)),
        "reproduces_run_id": reproduces,
    })
    project.db.log_event("run_prepared", run_id, job=job_id, mesh=mesh_id)
    if launch and mesh_job is None:
        jm.launch(job_id)
    return {"run_id": run_id, "job_id": job_id, "mesh_id": mesh_id, "mesh_job_id": mesh_job, "warnings": rc.warnings,
            "assumptions": rc.assumptions, "overrides": gc.overrides.__dict__}


def run_record(project: Project, run_id: str, include_snapshot: bool = False) -> dict[str, Any]:
    r = dec(project.db.one("SELECT * FROM runs WHERE id = ?", (run_id,)))
    if r is None:
        raise AeroBenchError.make("run.unknown", ErrorCategory.PROJECT, f"Unknown run {run_id}.")
    if not include_snapshot:
        r.pop("config_snapshot", None)
    return r


def run_config(project: Project, run_id: str) -> ProjectConfig:
    r = dec(project.db.one("SELECT config_snapshot_json FROM runs WHERE id = ?", (run_id,)))
    return ProjectConfig.model_validate(r["config_snapshot"])


def ingest_run(project: Project, run_id: str) -> dict[str, Any]:
    row = run_record(project, run_id, include_snapshot=True)
    cfg = ProjectConfig.model_validate(row["config_snapshot"])
    jm = JobManager(project)
    status = jm.refresh(row["job_id"])
    st = jm.state(row["job_id"])
    rdir = project.root / row["path"]
    solve_log = ""
    if st:
        for s in reversed(st.steps):
            if s.name in ("solve", "solve_first_order") and s.log:
                p = project.job_dir(row["job_id"]) / s.log
                if p.exists():
                    solve_log = p.read_text(errors="replace")
                    break
    log_state = parse_solver_log(solve_log) if solve_log else None
    ground = (row.get("resolved") or {}).get("ground_z", 0.0)
    res = ingest_run_results(rdir, cfg, ground, cfg.numerics.iterations, bool(log_state and log_state.nan_detected))
    diags = [d.model_dump(mode="json") for d in (st.diagnostics if st else [])]
    if res.get("available") and res["convergence"]["status"] == "diverged":
        diags.append(Diagnostic(code="solver.divergence", category=ErrorCategory.SOLVER, summary=res["convergence"]["summary"],
                                action="Use the Robust numerics preset and check mesh quality.").model_dump(mode="json"))
    run_status = {
        JobStatus.COMPLETED: "completed",
        JobStatus.FAILED: "failed",
        JobStatus.STOPPED: "stopped",
        JobStatus.INTERRUPTED: "interrupted",
        JobStatus.CANCELLED: "cancelled",
    }.get(status, status.value)
    if run_status == "completed" and not res.get("available"):
        run_status = "failed"
        diags.append(Diagnostic(code="results.missing", category=ErrorCategory.SOLVER,
                                summary="The solver finished but no force data was written.").model_dump(mode="json"))
    wall = None
    if st and st.started_at and st.ended_at:
        import datetime as dt

        wall = (dt.datetime.fromisoformat(st.ended_at) - dt.datetime.fromisoformat(st.started_at)).total_seconds()
    project.db.update("runs", "id", run_id, {
        "status": run_status, "started_at": st.started_at if st else None, "ended_at": st.ended_at if st else None,
        "wall_seconds": wall, "iterations": res.get("iterations"), "metrics_json": res if res.get("available") else None,
        "convergence_json": res.get("convergence"), "diagnostics_json": diags,
    })
    if run_status == "completed" and not project.config.reference.locked:
        project.update(lambda c: setattr(c.reference, "locked", True), snapshot=False)
    project.db.log_event("run_ingested", run_id, status=run_status)
    return {"run_id": run_id, "status": run_status, "results": res, "diagnostics": diags}


def reproduce_run(project: Project, ctx: AppContext, run_id: str, launch: bool = False) -> dict[str, Any]:
    """New run with the identical frozen configuration (BUILD_SPEC §33)."""
    cfg = run_config(project, run_id)
    return prepare_run(project, ctx, cfg, label=f"Reproduce {run_id}", reproduces=run_id, launch=launch)


def resume_run(project: Project, ctx: AppContext, run_id: str, extra_iterations: int = 0, launch: bool = False) -> dict[str, Any]:
    """Continue a stopped/interrupted/completed run from its latest time directory."""
    row = run_record(project, run_id, include_snapshot=True)
    cfg = ProjectConfig.model_validate(row["config_snapshot"])
    rdir = project.root / row["path"]
    times = [p for p in rdir.iterdir() if p.is_dir() and _is_time(p.name) and float(p.name) > 0]
    proc_times = [p for p in rdir.glob("processor0/*") if p.is_dir() and _is_time(p.name) and float(p.name) > 0]
    if not times and not proc_times:
        raise AeroBenchError.make("run.not_resumable", ErrorCategory.SOLVER, "This run has no saved time step to resume from.",
                                  action="Start a new run instead.")
    provider, pinfo = ctx.require_provider(row["provider"])
    adapter = get_adapter(cfg.solver.fork.value, cfg.solver.version, ctx.settings.allow_experimental_adapters)
    target = cfg.numerics.iterations + int(extra_iterations)
    n = int(row["n_procs"] or 1)
    steps: list = [CopyStep(name="schemes_final", src="system/fvSchemes.final", dst="system/fvSchemes", optional=True),
                   DictSetStep(name="end_time", file="system/controlDict", key_path="endTime", value=target),
                   RemoveStep(name="clear_stop_trigger", globs=["aerobenchStop"])]
    if n > 1 and not proc_times:
        steps.append(FoamStep(name="decomposePar", app=["decomposePar", "-force", "-latestTime"], stage="solve"))
    steps += [s for s in solve_steps(cfg, adapter, rdir, n, provider.staging == "staged", 0, resume=True)]
    jm = JobManager(project)
    job_id = jm.create("solve", rdir, steps, provider.serialise(), cores=n, target_kind="run", target_id=run_id,
                       meta={"resume": True, "iterations": target})
    snap = cfg.model_copy(deep=True)
    snap.numerics.iterations = target
    project.db.update("runs", "id", run_id, {"status": "queued", "job_id": job_id, "config_snapshot_json": snap.to_json()})
    project.db.log_event("run_resumed", run_id, job=job_id, target=target)
    if launch:
        jm.launch(job_id)
    return {"run_id": run_id, "job_id": job_id, "target_iterations": target}


def _is_time(name: str) -> bool:
    try:
        float(name)
        return True
    except ValueError:
        return False
