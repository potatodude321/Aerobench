"""Geometry routes: import, units, orientation, repair, components, preview, export."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..geometry.inspect import inspect_surface
from ..geometry.io import write_obj, write_stl
from ..geometry.pose import posed_components
from ..geometry.trisurface import TriSurface
from ..services import geometry as geo
from ..viz.payload import pack
from .app import EngineState, engine

router = APIRouter(prefix="/api/p/{pid}/geometry")


class ImportBody(BaseModel):
    path: str
    units: str = "auto"
    forward_axis: Optional[str] = None
    up_axis: Optional[str] = None
    identify: bool = True
    category: Optional[str] = None


@router.post("/import")
def import_geometry(pid: str, body: ImportBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.import_geometry(p, Path(body.path), body.units, body.forward_axis, body.up_axis, body.identify, category=body.category)


@router.post("/upload")
async def upload_geometry(pid: str, file: UploadFile = File(...), units: str = Form("auto"), e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    suffix = Path(file.filename or "upload.stl").suffix
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / (Path(file.filename or "upload").name or f"upload{suffix}")
        with open(tmp, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        with e.projects.lock(pid):
            return geo.import_geometry(p, tmp, units)


@router.get("")
def geometry_state(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    cfg = p.config
    current = None
    if cfg.geometry.revision_id:
        rev = p.geometry_revision(cfg.geometry.revision_id)
        current = {k: v for k, v in rev.items()}
    return {
        "revisions": [{k: v for k, v in r.items() if k not in ("report",)} for r in p.geometry_revisions()],
        "current": current,
        "transform": cfg.geometry.transform.model_dump(mode="json"),
        "components": [c.model_dump(mode="json") for c in cfg.geometry.components],
        "wheels": [w.model_dump(mode="json") for w in cfg.vehicle.wheels],
        "summary": cfg.geometry.summary.model_dump(mode="json") if cfg.geometry.summary else None,
        "reference": cfg.reference.model_dump(mode="json"),
    }


class UnitsBody(BaseModel):
    unit: str


@router.post("/units")
def set_units(pid: str, body: UnitsBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.set_units(p, body.unit)


class OrientationBody(BaseModel):
    forward_axis: str
    up_axis: str


@router.post("/orientation")
def set_orientation(pid: str, body: OrientationBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.set_orientation(p, body.forward_axis, body.up_axis)


class RepairBody(BaseModel):
    steps: Optional[list[tuple[str, dict[str, Any]]]] = None


@router.post("/repair")
def repair(pid: str, body: RepairBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.apply_repair(p, body.steps)


@router.post("/identify")
def identify(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.identify_and_derive(p, identify=True)


@router.post("/derive")
def derive(pid: str, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.refresh_derived(p)


class ComponentsBody(BaseModel):
    components: list[dict[str, Any]]
    wheels: Optional[list[dict[str, Any]]] = None


@router.post("/components")
def set_components(pid: str, body: ComponentsBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    with e.projects.lock(pid):
        return geo.set_components(p, body.components, body.wheels)


class RevisionBody(BaseModel):
    revision_id: str


@router.post("/activate")
def activate_revision(pid: str, body: RevisionBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    p.geometry_revision(body.revision_id)
    with e.projects.lock(pid):
        p.update(lambda c: setattr(c.geometry, "revision_id", body.revision_id))
        return geo.identify_and_derive(p, identify=False)


@router.get("/preview.abg")
def preview(pid: str, max_faces: int = 400000, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    header, blob = geo.preview_payload(p, max_faces=max_faces)
    return Response(pack(header, blob), media_type="application/octet-stream")


@router.get("/regions.abg")
def regions_preview(pid: str, max_faces: int = 400000, e: EngineState = Depends(engine)):
    """Raw regions (before component assignment) in the CFD frame, coloured by region."""
    import numpy as np

    from ..geometry import repair as repair_mod
    from ..viz.payload import encode_mesh

    p = e.projects.get(pid)
    s = p.cfd_surface()
    if s.n_faces > max_faces:
        lo, hi = s.bbox()
        s, _ = repair_mod.simplify_vertex_clustering(s, float(np.linalg.norm(hi - lo)) / (max_faces ** 0.5) * 0.9)
    header, blob = encode_mesh(s.points.astype(np.float32), s.faces.astype(np.uint32), part_ids=s.regions.astype(np.uint16),
                               part_names=s.region_names)
    return Response(pack(header, blob), media_type="application/octet-stream")


class ExportBody(BaseModel):
    path: str
    format: str = "stl"
    revision_id: Optional[str] = None
    posed: bool = True


@router.post("/export")
def export(pid: str, body: ExportBody, e: EngineState = Depends(engine)):
    p = e.projects.get(pid)
    cfg = p.config
    if body.posed:
        parts, _, _ = posed_components(p.cfd_surface(body.revision_id), cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
        surf = TriSurface.merge(list(parts.values()))
    else:
        surf = p.raw_surface(body.revision_id)
    out = Path(body.path)
    if body.format == "obj":
        write_obj(out, surf)
    else:
        write_stl(out, surf, binary=body.format == "stl_binary")
    return {"path": str(out), "faces": surf.n_faces, "report": inspect_surface(surf).as_dict()["diagnostics"]}
