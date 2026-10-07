"""Visualisation routes (ParaView sidecar)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from ..viz.presets import FIELD_INFO
from .app import EngineState, engine

router = APIRouter(prefix="/api/p/{pid}")


@router.get("/viz/status")
def viz_status(pid: str, e: EngineState = Depends(engine)):
    return e.viz.available()


@router.get("/viz/fields")
def viz_fields(pid: str):
    return FIELD_INFO


@router.get("/runs/{run_id}/viz/presets")
def presets(pid: str, run_id: str, e: EngineState = Depends(engine)):
    return e.viz.presets(e.projects.get(pid), run_id)


class ExtractBody(BaseModel):
    op: str
    params: dict[str, Any]


@router.post("/runs/{run_id}/viz/extract")
def extract(pid: str, run_id: str, body: ExtractBody, e: EngineState = Depends(engine)):
    return e.viz.extract(e.projects.get(pid), run_id, body.op, body.params)


@router.get("/runs/{run_id}/viz/payload/{key}")
def payload(pid: str, run_id: str, key: str, e: EngineState = Depends(engine)):
    return FileResponse(e.viz.payload_path(e.projects.get(pid), run_id, key), media_type="application/octet-stream")


class LineBody(BaseModel):
    p1: list[float]
    p2: list[float]
    resolution: int = 200
    fields: list[str] = ["p", "U"]


@router.post("/runs/{run_id}/viz/line")
def line(pid: str, run_id: str, body: LineBody, e: EngineState = Depends(engine)):
    return e.viz.line_sample(e.projects.get(pid), run_id, **body.model_dump())


class ProbeBody(BaseModel):
    points: list[list[float]]
    fields: list[str] = ["p", "U"]


@router.post("/runs/{run_id}/viz/probe")
def probe(pid: str, run_id: str, body: ProbeBody, e: EngineState = Depends(engine)):
    return e.viz.probe(e.projects.get(pid), run_id, **body.model_dump())


class ShotBody(BaseModel):
    preset_id: Optional[str] = None
    items: Optional[list[dict[str, Any]]] = None
    camera: Optional[dict[str, Any]] = None
    size: list[int] = [1600, 900]


@router.post("/runs/{run_id}/viz/screenshot")
def screenshot(pid: str, run_id: str, body: ShotBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    if body.preset_id:
        path = e.viz.preset_screenshot(p, run_id, body.preset_id, size=tuple(body.size))
    else:
        path = e.viz.screenshot(p, run_id, body.items or [], body.camera, tuple(body.size))
    return {"path": str(path), "name": path.name if path else None}


@router.get("/runs/{run_id}/viz/image/{name}")
def image(pid: str, run_id: str, name: str, e: EngineState = Depends(engine)):
    import re

    p = e.projects.get(pid)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}\.png", name):
        return PlainTextResponse("bad name", status_code=400)
    return FileResponse(p.cache_dir("images", run_id) / name, media_type="image/png")


@router.post("/runs/{run_id}/viz/paraview-gui")
def paraview_gui(pid: str, run_id: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    return {"script": str(e.viz.paraview_gui_script(p, run_id))}


@router.get("/runs/{run_id}/viz/script")
def script(pid: str, run_id: str, presets: str = "cp_body", e: EngineState = Depends(engine)):
    return PlainTextResponse(e.viz.expert_script(e.projects.get(pid), run_id, presets.split(",")))


@router.get("/scenes")
def scenes(pid: str, e: EngineState = Depends(engine)):
    return e.viz.scenes(e.projects.get(pid))


class SceneBody(BaseModel):
    id: Optional[str] = None
    name: str
    scene: dict[str, Any]


@router.post("/scenes")
def save_scene(pid: str, body: SceneBody, e: EngineState = Depends(engine)):
    return {"id": e.viz.save_scene(e.projects.get(pid), body.scene, body.id, body.name)}
