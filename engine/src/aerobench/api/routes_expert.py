"""Expert Mode routes: case browser, generated-file preview, diffs, versions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ..core.errors import AeroBenchError, ErrorCategory
from ..foam.adapters import get_adapter
from ..foam.case.builder import build_mesh_case, build_solve_case
from ..foam.case.generated import diff_text
from ..foam.parser import parse, strip_header
from ..foam.ast import to_json
from ..project.db import dec
from ..services.mesh import plan_mesh
from ..services.runs import run_record
from .app import EngineState, engine

router = APIRouter(prefix="/api/p/{pid}/expert")

MAX_TEXT = 2_000_000


def _case_dir(p, kind: str, ident: str) -> Path:
    if kind == "run":
        return p.root / run_record(p, ident)["path"]
    if kind == "mesh":
        r = p.db.one("SELECT path FROM mesh_revisions WHERE id = ?", (ident,))
        if r is None:
            raise AeroBenchError.make("mesh.unknown", ErrorCategory.PROJECT, f"Unknown mesh {ident}.")
        return p.root / r["path"]
    raise AeroBenchError.make("expert.kind", ErrorCategory.CONFIGURATION, "kind must be 'run' or 'mesh'")


def _safe(case: Path, rel: str) -> Path:
    f = (case / rel).resolve()
    if case.resolve() not in f.parents and f != case.resolve():
        raise AeroBenchError.make("expert.path", ErrorCategory.CONFIGURATION, "Path outside the case.")
    return f


@router.get("/tree/{kind}/{ident}")
def tree(pid: str, kind: str, ident: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    case = _case_dir(p, kind, ident)
    out = []
    for top in ("0", "constant", "system", "postProcessing"):
        d = case / top
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and "polyMesh" not in f.parts or (f.is_file() and f.name in ("boundary",)):
                out.append({"path": f.relative_to(case).as_posix(), "size": f.stat().st_size})
    times = sorted([x.name for x in case.iterdir() if x.is_dir() and _is_num(x.name)], key=float)
    return {"case": str(case), "files": out, "times": times, "processors": sorted(x.name for x in case.glob("processor*"))}


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


@router.get("/file/{kind}/{ident}")
def file(pid: str, kind: str, ident: str, path: str, structured: bool = False, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    f = _safe(_case_dir(p, kind, ident), path)
    if not f.exists():
        raise AeroBenchError.make("expert.file_missing", ErrorCategory.PROJECT, f"{path} does not exist.")
    if f.stat().st_size > MAX_TEXT:
        return {"path": path, "text": None, "too_large": True, "size": f.stat().st_size}
    text = f.read_text(errors="replace")
    out = {"path": path, "text": text}
    if structured:
        try:
            hdr, body = strip_header(parse(text))
            out["structured"] = to_json(body)
        except Exception as exc:  # noqa: BLE001
            out["parse_error"] = str(exc)
    return out


@router.get("/preview/{stage}")
def preview(pid: str, stage: str, e: EngineState = Depends(engine)):
    """Render the dictionaries the *current* configuration would generate (nothing written)."""
    p = e.projects.get(pid)
    cfg = p.config
    plan, rc, _ = plan_mesh(p, cfg)
    adapter = get_adapter(cfg.solver.fork.value, cfg.solver.version, e.ctx.settings.allow_experimental_adapters)
    gc = build_mesh_case(cfg, rc, adapter) if stage == "mesh" else build_solve_case(cfg, rc, adapter)
    return {"stage": stage, "files": gc.render_all(), "overrides": gc.overrides.__dict__, "meta": gc.meta}


@router.get("/diff")
def diff(pid: str, run_a: str, run_b: str, path: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    a = _safe(_case_dir(p, "run", run_a), path)
    b = _safe(_case_dir(p, "run", run_b), path)
    ta = a.read_text(errors="replace") if a.exists() else ""
    tb = b.read_text(errors="replace") if b.exists() else ""
    return PlainTextResponse(diff_text(ta, tb, path) or "(identical)")


@router.get("/diff-current/{run_id}")
def diff_current(pid: str, run_id: str, e: EngineState = Depends(engine)):
    """What would change if this run were regenerated from the current configuration."""
    p = e.projects.get(pid)
    cfg = p.config
    plan, rc, _ = plan_mesh(p, cfg)
    adapter = get_adapter(cfg.solver.fork.value, cfg.solver.version, e.ctx.settings.allow_experimental_adapters)
    files = build_solve_case(cfg, rc, adapter).render_all()
    case = _case_dir(p, "run", run_id)
    out = {}
    for rel, text in files.items():
        old = (case / rel).read_text(errors="replace") if (case / rel).exists() else ""
        d = diff_text(old, text, rel)
        if d:
            out[rel] = d
    return out
