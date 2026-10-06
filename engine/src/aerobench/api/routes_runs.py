"""Mesh, run and job routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..jobs.manager import JobManager
from ..jobs.worker import ensure_worker_process
from ..project.db import dec
from ..results.ingest import series_payload
from ..services import mesh as mesh_svc
from ..services import runs as run_svc
from .app import EngineState, engine

router = APIRouter(prefix="/api/p/{pid}")


# --------------------------------------------------------------------------- mesh


@router.get("/mesh/plan")
def mesh_plan(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    plan, rc, ext = mesh_svc.plan_mesh(p)
    existing = mesh_svc.find_mesh(p, p.config, ("completed", "queued", "running", "starting"))
    return {"plan": plan.as_dict(), "resolved": rc.summary(), "existing": {k: existing[k] for k in ("id", "status")} if existing else None,
            "ground": ext["ground"]}


class MeshBody(BaseModel):
    force: bool = False
    nprocs: Optional[int] = None


@router.post("/meshes")
def create_mesh(pid: str, body: MeshBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        res = mesh_svc.prepare_mesh(p, e.ctx, force=body.force, nprocs=body.nprocs)
    ensure_worker_process(p.root)
    return res


@router.get("/meshes")
def list_meshes(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    rows = [dec(r) for r in p.db.all("SELECT id, created_at, geometry_revision_id, input_hash, status, job_id, cells, provider, "
                                         "foam_version, diagnostics_json, snappy_json FROM mesh_revisions ORDER BY created_at DESC")]
    for r in rows:
        sn = r.pop("snappy", None) or {}
        r["layer_coverage"] = sn.get("overall_layer_coverage")
    return rows


@router.get("/meshes/{mesh_id}")
def get_mesh(pid: str, mesh_id: str, e: EngineState = Depends(engine)):
    return mesh_svc.mesh_record(e.projects.get(pid), mesh_id)


# --------------------------------------------------------------------------- runs


class RunBody(BaseModel):
    label: Optional[str] = None
    config_patch: Optional[dict[str, Any]] = None  # one-off overrides for this run (not saved to the project)


@router.post("/runs")
def create_run(pid: str, body: RunBody, e: EngineState = Depends(engine)):
    from ..domain.project import ProjectConfig
    from ..domain.templates import deep_merge

    p = e.projects.get(pid)
    cfg = p.config
    if body.config_patch:
        cfg = ProjectConfig.model_validate(deep_merge(cfg.to_json(), body.config_patch))
    with e.projects.lock(pid):
        res = run_svc.prepare_run(p, e.ctx, cfg, label=body.label)
    ensure_worker_process(p.root)
    return res


@router.get("/runs")
def list_runs(pid: str, study_id: Optional[str] = None, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    q = ("SELECT id, created_at, label, status, job_id, mesh_revision_id, geometry_revision_id, operating_json, provider, "
         "foam_version, n_procs, started_at, ended_at, wall_seconds, iterations, metrics_json, convergence_json, study_id, design_id, "
         "tags_json, notes FROM runs")
    rows = p.db.all(q + (" WHERE study_id = ?" if study_id else "") + " ORDER BY created_at DESC", (study_id,) if study_id else ())
    out = []
    for r in rows:
        d = dec(r)
        met = (d.pop("metrics", None) or {})
        d["metrics"] = met.get("metrics")
        d["balance_ill_conditioned"] = (met.get("result") or {}).get("balance_ill_conditioned")
        conv = d.pop("convergence", None) or {}
        d["convergence_status"] = conv.get("status")
        out.append(d)
    return out


@router.get("/runs/{run_id}")
def get_run(pid: str, run_id: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    r = run_svc.run_record(p, run_id)
    jm = JobManager(p)
    if r.get("job_id"):
        st = jm.state(r["job_id"])
        r["job"] = st.model_dump(mode="json") if st else None
    return r


@router.get("/runs/{run_id}/config")
def get_run_config(pid: str, run_id: str, e: EngineState = Depends(engine)):
    return run_svc.run_config(e.projects.get(pid), run_id).to_json()


@router.get("/runs/{run_id}/series")
def run_series(pid: str, run_id: str, max_points: int = 4000, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    r = run_svc.run_record(p, run_id, include_snapshot=True)
    from ..domain.project import ProjectConfig

    cfg = ProjectConfig.model_validate(r["config_snapshot"])
    ground = (r.get("resolved") or {}).get("ground_z", 0.0)
    return series_payload(p.root / r["path"], cfg, ground, max_points)


class StopBody(BaseModel):
    force: bool = False


@router.post("/runs/{run_id}/stop")
def stop_run(pid: str, run_id: str, body: StopBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    r = run_svc.run_record(p, run_id)
    JobManager(p).request_stop(r["job_id"], force=body.force)
    return {"ok": True}


class ResumeBody(BaseModel):
    extra_iterations: int = 0


@router.post("/runs/{run_id}/resume")
def resume_run(pid: str, run_id: str, body: ResumeBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        res = run_svc.resume_run(p, e.ctx, run_id, body.extra_iterations)
    ensure_worker_process(p.root)
    return res


@router.post("/runs/{run_id}/reproduce")
def reproduce_run(pid: str, run_id: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        res = run_svc.reproduce_run(p, e.ctx, run_id)
    ensure_worker_process(p.root)
    return res


@router.post("/runs/{run_id}/reingest")
def reingest_run(pid: str, run_id: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    return run_svc.ingest_run(p, run_id)


class RunMeta(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None


@router.patch("/runs/{run_id}")
def update_run(pid: str, run_id: str, body: RunMeta, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    ch: dict[str, Any] = {}
    if body.label is not None:
        ch["label"] = body.label
    if body.notes is not None:
        ch["notes"] = body.notes
    if body.tags is not None:
        ch["tags_json"] = body.tags
    p.db.update("runs", "id", run_id, ch)
    return run_svc.run_record(p, run_id)


# --------------------------------------------------------------------------- jobs


@router.get("/jobs")
def list_jobs(pid: str, active: bool = False, e: EngineState = Depends(engine)):
    jm = JobManager(e.projects.get(pid))
    return jm.list(["queued", "starting", "running", "stopping"] if active else None)


@router.get("/jobs/{job_id}")
def get_job(pid: str, job_id: str, e: EngineState = Depends(engine)):
    jm = JobManager(e.projects.get(pid))
    st = jm.state(job_id)
    return {"row": jm.row(job_id), "state": st.model_dump(mode="json") if st else None}


@router.get("/jobs/{job_id}/log")
def job_log(pid: str, job_id: str, step: Optional[int] = None, offset: int = -1, e: EngineState = Depends(engine)):
    return JobManager(e.projects.get(pid)).log_tail(job_id, step, offset)


@router.get("/jobs/{job_id}/commands")
def job_commands(pid: str, job_id: str, e: EngineState = Depends(engine)):
    return JobManager(e.projects.get(pid)).commands(job_id)


@router.post("/jobs/{job_id}/stop")
def stop_job(pid: str, job_id: str, body: StopBody, e: EngineState = Depends(engine)):
    JobManager(e.projects.get(pid)).request_stop(job_id, force=body.force)
    return {"ok": True}


@router.post("/worker/ensure")
def worker_ensure(pid: str, e: EngineState = Depends(engine)):
    return {"started_pid": ensure_worker_process(e.projects.get(pid).root)}
