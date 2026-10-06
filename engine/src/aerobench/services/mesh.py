"""Meshing workflow: plan → generated mesh case → job → ingestion (quality, layers, leaks)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, Diagnostic, ErrorCategory, Severity, raise_if_errors
from ..core.fs import sha256_tree
from ..core.ids import short_id, utc_now_iso
from ..domain.project import ProjectConfig, mesh_stage_input, validate_project
from ..core.fs import sha256_json
from ..foam.adapters import get_adapter
from ..foam.case.builder import ResolvedCase, assert_no_dynamic_code, build_mesh_case, resolve_case
from ..foam.logs.mesh_logs import parse_checkmesh, parse_snappy
from ..geometry.pose import write_case_surfaces
from ..jobs.manager import JobManager
from ..jobs.spec import FoamStep, JobStatus, RemoveStep, StageStep
from ..meshing.plan import MeshPlan, resolve_mesh_plan
from ..project.db import dec
from ..project.store import Project
from .context import AppContext
from .geometry import vehicle_extent

LAYER_COVERAGE_WARN = 0.7
LAYER_COVERAGE_POOR = 0.4


def mesh_hash(cfg: ProjectConfig) -> str:
    return sha256_json(mesh_stage_input(cfg))


def plan_mesh(project: Project, cfg: Optional[ProjectConfig] = None) -> tuple[MeshPlan, ResolvedCase, dict[str, Any]]:
    cfg = cfg or project.config
    ext = vehicle_extent(project, cfg)
    frontal = cfg.reference.area_computed or cfg.reference.area
    plan = resolve_mesh_plan(cfg, ext["bbox_min"], ext["bbox_max"], ext["ground_z"], ext["areas"], ext["clearance"], frontal)
    rc = resolve_case(cfg, plan)
    return plan, rc, ext


def find_mesh(project: Project, cfg: Optional[ProjectConfig] = None, statuses=("completed",)) -> Optional[dict[str, Any]]:
    h = mesh_hash(cfg or project.config)
    q = "SELECT * FROM mesh_revisions WHERE input_hash = ? AND status IN (%s) ORDER BY created_at DESC LIMIT 1" % ",".join("?" * len(statuses))
    return dec(project.db.one(q, (h, *statuses)))


def mesh_steps(nprocs: int, staged: bool) -> list:
    steps: list = []
    if staged:
        steps.append(StageStep(name="stage_in", direction="in"))
    steps.append(FoamStep(name="surfaceFeatureExtract", app=["surfaceFeatureExtract"], stage="mesh"))
    steps.append(FoamStep(name="blockMesh", app=["blockMesh"], stage="mesh"))
    if nprocs > 1:
        steps.append(RemoveStep(name="clean_processors", globs=["processor*"]))
        steps.append(FoamStep(name="decomposePar", app=["decomposePar", "-force"], stage="mesh"))
        steps.append(FoamStep(name="snappyHexMesh", app=["snappyHexMesh", "-overwrite"], nprocs=nprocs, stage="mesh"))
        steps.append(FoamStep(name="reconstructParMesh", app=["reconstructParMesh", "-constant"], stage="mesh"))
        steps.append(RemoveStep(name="remove_processors", globs=["processor*"]))
    else:
        steps.append(FoamStep(name="snappyHexMesh", app=["snappyHexMesh", "-overwrite"], stage="mesh"))
    steps.append(FoamStep(name="checkMesh", app=["checkMesh"], stage="mesh"))
    if staged:
        steps.append(StageStep(name="stage_out", direction="out"))
    return steps


def prepare_mesh(project: Project, ctx: AppContext, cfg: Optional[ProjectConfig] = None, force: bool = False,
                 nprocs: Optional[int] = None, launch: bool = False) -> dict[str, Any]:
    cfg = (cfg or project.config).model_copy(deep=True)
    issues = validate_project(cfg, "mesh")
    raise_if_errors(issues, "mesh configuration")
    h = mesh_hash(cfg)
    if not force:
        existing = find_mesh(project, cfg, ("completed", "queued", "running"))
        if existing:
            return {"mesh_id": existing["id"], "job_id": existing.get("job_id"), "reused": True, "status": existing["status"]}
    provider, pinfo = ctx.require_provider(cfg.execution.provider_id)
    adapter = get_adapter(cfg.solver.fork.value, cfg.solver.version, ctx.settings.allow_experimental_adapters)
    n = max(1, min(nprocs or cfg.execution.n_procs, ctx.max_cores()))
    if n > 1 and not pinfo.mpi.available:
        n = 1
    cfg.execution.n_procs = n
    for d in provider.check_case(project.root, n):
        raise AeroBenchError(d)
    mesh_id = short_id("mesh")
    mdir = project.mesh_dir(mesh_id)
    (mdir / "constant" / "triSurface").mkdir(parents=True)
    cfd = project.cfd_surface(cfg=cfg)
    cs = write_case_surfaces(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction, mdir / "constant" / "triSurface")
    plan, rc, ext = plan_mesh(project, cfg)
    gc = build_mesh_case(cfg, rc, adapter)
    assert_no_dynamic_code(gc)
    manifest = gc.write(mdir)
    steps = mesh_steps(n, provider.staging == "staged")
    job_id = JobManager(project).create("mesh", mdir, steps, provider.serialise(), cores=n, target_kind="mesh", target_id=mesh_id,
                                       meta={"mesh_input_hash": h})
    project.db.insert("mesh_revisions", {
        "id": mesh_id, "created_at": utc_now_iso(), "geometry_revision_id": cfg.geometry.revision_id, "input_hash": h,
        "status": "queued", "job_id": job_id, "path": str(mdir.relative_to(project.root)), "plan_json": plan.as_dict(),
        "provider": provider.id, "foam_fork": pinfo.fork, "foam_version": pinfo.version, "manifest_json": manifest,
        "config_snapshot_json": cfg.to_json(), "resolved_json": rc.summary(),
        "diagnostics_json": [w for w in cs.warnings] + list(plan.warnings),
    })
    project.db.log_event("mesh_prepared", mesh_id, job=job_id, cells_estimate=plan.estimate_cells)
    if launch:
        JobManager(project).launch(job_id)
    return {"mesh_id": mesh_id, "job_id": job_id, "reused": False, "status": "queued", "plan": plan.as_dict(),
            "overrides": gc.overrides.__dict__}


def _step_log(project: Project, job_id: str, step_name: str) -> str:
    jm = JobManager(project)
    st = jm.state(job_id)
    if st is None:
        return ""
    for s in st.steps:
        if s.name == step_name and s.log:
            p = project.job_dir(job_id) / s.log
            return p.read_text(errors="replace") if p.exists() else ""
    return ""


def ingest_mesh(project: Project, mesh_id: str) -> dict[str, Any]:
    row = dec(project.db.one("SELECT * FROM mesh_revisions WHERE id = ?", (mesh_id,)))
    if row is None:
        raise AeroBenchError.make("mesh.unknown", ErrorCategory.PROJECT, f"Unknown mesh {mesh_id}.")
    jm = JobManager(project)
    job_status = jm.refresh(row["job_id"])
    st = jm.state(row["job_id"])
    diags: list[Diagnostic] = [d for d in (st.diagnostics if st else [])]
    snappy = parse_snappy(_step_log(project, row["job_id"], "snappyHexMesh"))
    check_txt = _step_log(project, row["job_id"], "checkMesh")
    check = parse_checkmesh(check_txt) if check_txt else None
    mdir = project.root / row["path"]
    status = "failed"
    mesh_digest = None
    if job_status == JobStatus.COMPLETED and check is not None and check.cells:
        status = "completed"
        mesh_digest = sha256_tree(mdir / "constant" / "polyMesh")
        diags.extend(quality_diagnostics(check, snappy, row, project))
        if any(d.code in ("mesh.leak", "mesh.disconnected") for d in diags):
            status = "failed"
    elif job_status in (JobStatus.STOPPED, JobStatus.INTERRUPTED):
        status = job_status.value
    project.db.update("mesh_revisions", "id", mesh_id, {
        "status": status,
        "cells": check.cells if check else snappy.final_cells,
        "quality_json": check.as_dict() if check else None,
        "snappy_json": snappy.as_dict(),
        "mesh_hash": mesh_digest,
        "diagnostics_json": [d.model_dump(mode="json") if isinstance(d, Diagnostic) else d for d in diags],
    })
    project.db.log_event("mesh_ingested", mesh_id, status=status)
    return {"mesh_id": mesh_id, "status": status, "cells": check.cells if check else None, "diagnostics": [d.model_dump(mode="json") for d in diags if isinstance(d, Diagnostic)]}


def quality_diagnostics(check, snappy, row: dict, project: Project) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    def add(code, sev, summary, cause="", action="", **ctx):
        out.append(Diagnostic(code=code, category=ErrorCategory.MESH, severity=sev, summary=summary, likely_cause=cause, action=action, context=ctx))

    if check.n_regions and check.n_regions > 1:
        add("mesh.disconnected", Severity.ERROR, f"The mesh has {check.n_regions} disconnected regions.",
            "Fluid trapped inside parts or between intersecting surfaces.", "Check geometry closure and component overlaps.")
    # leak test: a leak meshes the vehicle interior, so the fluid volume ≈ the whole domain
    plan = row.get("plan") or {}
    if check.total_volume and plan:
        (x0, y0, z0), (x1, y1, z1) = plan["domain_min"], plan["domain_max"]
        dom = (x1 - x0) * (y1 - y0) * (z1 - z0)
        try:
            cfg = ProjectConfig.model_validate(row["config_snapshot"])
            body_vol = _closed_vehicle_volume(project, cfg)
        except Exception:  # noqa: BLE001
            body_vol = None
        if body_vol and body_vol > 0:
            if half := plan.get("half_model"):
                body_vol *= 0.5
            if check.total_volume > dom - 0.5 * body_vol:
                add("mesh.leak", Severity.ERROR, "The mesh leaked into the vehicle interior.",
                    "An opening in the vehicle surface larger than the local cell size let the mesh flow inside.",
                    "Repair/close the geometry (Geometry → Repair → fill holes) or refine the surface so gaps are resolved.",
                    mesh_volume=check.total_volume, domain_volume=dom, vehicle_volume=body_vol)
    if check.max_non_ortho is not None and check.max_non_ortho > 75:
        add("mesh.non_orthogonality", Severity.WARNING, f"Maximum non-orthogonality is {check.max_non_ortho:.1f}°.",
            "Distorted cells near snapped features or collapsed layers.", "Use the Robust numerics preset or adjust snapping/layer controls.")
    if check.highly_skew_faces:
        add("mesh.skewness", Severity.WARNING if check.highly_skew_faces > 0 else Severity.INFO,
            f"{check.highly_skew_faces} highly skewed faces (max skewness {check.max_skewness:.2f}).",
            "Typically located at snapped sharp edges; checkMesh flags skewness > 4.",
            "Usually acceptable for steady RANS; if the solution oscillates, refine features or use the Robust preset.",
            count=check.highly_skew_faces)
    cov = snappy.overall_layer_coverage()
    if cov is not None:
        if cov < LAYER_COVERAGE_POOR:
            add("mesh.layers_poor", Severity.WARNING, f"Prism-layer coverage is only {cov * 100:.0f}%.",
                "Layers collapse at sharp edges, thin gaps and where the first layer is too thick relative to the surface cell.",
                "Lower the expansion ratio, reduce the number of layers, or refine the surface.", coverage=cov)
        elif cov < LAYER_COVERAGE_WARN:
            add("mesh.layers_partial", Severity.INFO, f"Prism-layer coverage is {cov * 100:.0f}%.", coverage=cov)
    return out


def _closed_vehicle_volume(project: Project, cfg: ProjectConfig) -> Optional[float]:
    from ..geometry.inspect import inspect_surface
    from ..geometry.pose import posed_components
    from ..domain.geometry import ComponentRole

    cfd = project.cfd_surface(cfg=cfg)
    parts, _, _ = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
    vol = 0.0
    for cid, s in parts.items():
        if cfg.geometry.component(cid).role != ComponentRole.BODY:
            continue
        rep = inspect_surface(s)
        if not rep.closed:
            return None
        vol += abs(s.signed_volume())
    return vol or None


def mesh_record(project: Project, mesh_id: str) -> dict[str, Any]:
    r = dec(project.db.one("SELECT * FROM mesh_revisions WHERE id = ?", (mesh_id,)))
    if r is None:
        raise AeroBenchError.make("mesh.unknown", ErrorCategory.PROJECT, f"Unknown mesh {mesh_id}.")
    r.pop("config_snapshot", None)
    return r
