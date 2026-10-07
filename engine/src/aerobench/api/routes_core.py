"""Core routes: health, environment, settings, templates, projects, configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..aero.forces import METRIC_INFO
from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import project_folder_name
from ..domain.project import ProjectConfig, validate_project
from ..domain.templates import apply_template, list_templates
from ..jobs.manager import JobManager
from ..jobs.worker import ensure_worker_process, lock_holder
from ..project.store import Project
from ..services import setup as setup_svc
from ..settings import AppSettings
from ..version import APP_DISPLAY_NAME, APP_VERSION, GENERATOR_VERSION
from .app import EngineState, engine

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"ok": True, "app": APP_DISPLAY_NAME, "version": APP_VERSION, "generator": GENERATOR_VERSION}


@router.get("/environment")
def environment(refresh: bool = False, e: EngineState = Depends(engine)):
    if refresh:
        e.ctx.providers(refresh=True)
        e.ctx.paraview(refresh=True)
    return e.ctx.environment_report()


@router.get("/settings")
def get_settings(e: EngineState = Depends(engine)):
    return e.ctx.settings.model_dump(mode="json")


@router.put("/settings")
def put_settings(body: dict[str, Any], e: EngineState = Depends(engine)):
    s = AppSettings.model_validate({**e.ctx.settings.model_dump(mode="json"), **body})
    s.save()
    e.ctx.settings = s
    e.ctx.providers(refresh=True)
    e.ctx.paraview(refresh=True)
    return s.model_dump(mode="json")


@router.get("/templates")
def templates():
    return list_templates()


@router.get("/metrics")
def metric_info():
    return METRIC_INFO


@router.get("/air")
def air(temperature_c: float = 20.0, pressure_pa: float = 101325.0):
    """Dry-air properties (ideal gas + Sutherland) — single source of truth for the UI."""
    from ..aero.flow import air_properties

    p = air_properties(temperature_c, pressure_pa)
    return {"density": round(p["density"], 5), "kinematic_viscosity": float(f"{p['kinematic_viscosity']:.5g}"),
            "dynamic_viscosity": p["dynamic_viscosity"], "temperature_c": temperature_c, "pressure_pa": pressure_pa}


@router.get("/numerics-presets")
def numerics_presets():
    return setup_svc.numerics_presets()


class CreateProject(BaseModel):
    """Either an exact ``path`` or a ``parent`` folder (the project folder name is derived from ``name``)."""

    name: str
    path: Optional[str] = None
    parent: Optional[str] = None
    template_id: Optional[str] = None


class OpenProject(BaseModel):
    path: str


def _project_payload(p: Project, e: EngineState) -> dict[str, Any]:
    return {
        "project_id": p.config.project_id,
        "root": str(p.root),
        "config": p.config.to_json(),
        "validation": [i.model_dump(mode="json") for i in validate_project(p.config)],
        "staleness": p.staleness(),
        "worker": lock_holder(p.root),
    }


@router.post("/projects")
def create_project(body: CreateProject, e: EngineState = Depends(engine)):
    cfg = ProjectConfig()
    if body.template_id:
        cfg = apply_template(cfg, body.template_id)
    if body.path:
        root = Path(body.path)
    elif body.parent:
        Path(body.parent).mkdir(parents=True, exist_ok=True)
        root = Path(body.parent) / project_folder_name(body.name)
    else:
        raise AeroBenchError.make("project.path_missing", ErrorCategory.PROJECT, "No folder was given for the new project.")
    if cfg.execution.n_procs == 1:  # templates may pin a count; otherwise use the machine
        cfg.execution.n_procs = e.ctx.default_cores(root)
    p = Project.create(root, body.name, cfg)
    e.projects.add(p)
    e.ctx.settings.add_recent(str(p.root))
    e.ctx.settings.save()
    return _project_payload(p, e)


@router.get("/projects/defaults")
def project_defaults(name: str = "", e: EngineState = Depends(engine)):
    parent = e.ctx.settings.default_projects_dir()
    return {"parent": parent, "folder_name": project_folder_name(name) if name else None,
            "path": str(Path(parent) / project_folder_name(name)) if name else None}


@router.post("/projects/open")
def open_project(body: OpenProject, e: EngineState = Depends(engine)):
    p = Project.open(Path(body.path))
    e.projects.add(p)
    changed = JobManager(p).reconcile()
    if JobManager(p).list(["queued", "starting", "running", "stopping"]):
        ensure_worker_process(p.root)
    e.ctx.settings.add_recent(str(p.root))
    e.ctx.settings.save()
    out = _project_payload(p, e)
    out["reconciled_jobs"] = changed
    return out


@router.get("/projects/recent")
def recent(e: EngineState = Depends(engine)):
    out = []
    for path in e.ctx.settings.recent_projects:
        pp = Path(path)
        out.append({"path": path, "exists": (pp / "project.json").exists(), "name": pp.name})
    return out


@router.get("/p/{pid}")
def get_project(pid: str, e: EngineState = Depends(engine)):
    return _project_payload(e.projects.get(pid), e)


@router.post("/p/{pid}/close")
def close_project(pid: str, e: EngineState = Depends(engine)):
    e.projects.remove(pid)
    return {"ok": True}


@router.patch("/p/{pid}/config")
def patch_config(pid: str, patch: dict[str, Any], e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        setup_svc.patch_config(p, patch)
    return _project_payload(p, e)


@router.post("/p/{pid}/presets/{kind}/{value}")
def apply_preset(pid: str, kind: str, value: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        setup_svc.apply_preset(p, kind, value)
        if kind in ("template", "tunnel", "mesh"):
            from ..services.geometry import refresh_derived

            if p.config.geometry.revision_id:
                refresh_derived(p)
    return _project_payload(p, e)


@router.get("/p/{pid}/derived")
def derived(pid: str, e: EngineState = Depends(engine)):
    return setup_svc.derived_quantities(e.projects.get(pid).config)


@router.get("/p/{pid}/events")
def events(pid: str, limit: int = 200, e: EngineState = Depends(engine)):
    from ..project.db import dec

    p = e.projects.get(pid)
    return [dec(r) for r in p.db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]


@router.get("/p/{pid}/autosaves")
def autosaves(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    return sorted([x.name for x in (p.root / ".autosave").glob("project-*.json")], reverse=True)
