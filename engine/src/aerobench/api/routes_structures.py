"""Aero element setup and the structural check (CalculiX)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import element as el
from ..services import structures as svc
from ..viz.payload import pack
from .app import EngineState, engine

router = APIRouter()


class ElementBody(BaseModel):
    environment: Optional[str] = None
    ground_clearance: Optional[float] = None
    angle_deg: Optional[float] = None
    free_stream_chords: Optional[float] = None


class StructureBody(BaseModel):
    structure: dict[str, Any]


class StartBody(BaseModel):
    label: str = ""


@router.patch("/api/p/{pid}/element")
def element_setup(pid: str, body: ElementBody, e: EngineState = Depends(engine)):
    with e.projects.lock(pid):
        return el.apply_element_setup(e.projects.get(pid), **body.model_dump())


# ------------------------------------------------------------------ materials (user library)
@router.get("/api/materials")
def materials(e: EngineState = Depends(engine)):
    return svc.materials_payload(None)


@router.put("/api/materials/{material_id}")
def material_save(material_id: str, body: dict[str, Any], e: EngineState = Depends(engine)):
    return svc.save_user_material({**body, "id": material_id})


@router.delete("/api/materials/{material_id}")
def material_delete(material_id: str, e: EngineState = Depends(engine)):
    svc.delete_user_material(material_id)
    return {"ok": True}


# ------------------------------------------------------------------ structure
@router.get("/api/p/{pid}/structure")
def structure_status(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    out = svc.status(p, e.ctx)
    out["materials"] = svc.materials_payload(p)
    return out


@router.put("/api/p/{pid}/structure")
def structure_set(pid: str, body: StructureBody, e: EngineState = Depends(engine)):
    with e.projects.lock(pid):
        return svc.set_structure(e.projects.get(pid), body.structure)


@router.get("/api/p/{pid}/structure/presets")
def structure_presets(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    return {"supports": svc.support_presets(p), "section": svc.default_section(p)}


@router.post("/api/p/{pid}/structure/prepare")
def structure_prepare(pid: str, e: EngineState = Depends(engine)):
    return svc.prepare(e.projects.get(pid), e.ctx, e.viz)


@router.get("/api/p/{pid}/structure/loads.abg")
def structure_loads(pid: str, e: EngineState = Depends(engine)):
    header, blob = svc.loads_preview(e.projects.get(pid), e.ctx, e.viz)
    return Response(pack(header, blob), media_type="application/octet-stream")


@router.post("/api/p/{pid}/structure/analyses")
def structure_start(pid: str, body: StartBody, e: EngineState = Depends(engine)):
    with e.projects.lock(pid):
        return svc.start(e.projects.get(pid), e.ctx, e.viz, body.label)


@router.get("/api/p/{pid}/structure/analyses")
def structure_list(pid: str, e: EngineState = Depends(engine)):
    return svc.list_analyses(e.projects.get(pid))


@router.get("/api/p/{pid}/structure/analyses/{sid}/result.abg")
def structure_result(pid: str, sid: str, field: str = "reserve", e: EngineState = Depends(engine)):
    header, blob = svc.result_payload(e.projects.get(pid), sid, field)
    return Response(pack(header, blob), media_type="application/octet-stream")
