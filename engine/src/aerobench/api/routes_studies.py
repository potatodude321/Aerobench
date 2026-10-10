"""Studies, optimisation, geometry parameters and reports (registered on the explore router)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from ..services import reports as rep
from ..services import studies as svc
from ..services import zones as zsvc
from ..viz.payload import pack
from .app import EngineState, engine


class StudyBody(BaseModel):
    kind: str  # study | optimisation
    definition: dict[str, Any]


class ParamsBody(BaseModel):
    parameters: list[dict[str, Any]]


class ParamPreviewBody(BaseModel):
    values: dict[str, float] = {}
    parameters: Optional[list[dict[str, Any]]] = None
    check: bool = True


class ZonesBody(BaseModel):
    zones: list[dict[str, Any]]


class ZonePreviewBody(BaseModel):
    values: dict[str, float] = {}
    zones: Optional[list[dict[str, Any]]] = None  # unsaved zones (default: the project's)
    check: bool = True


class ReportBody(BaseModel):
    kind: str  # run | compare | study
    title: Optional[str] = None
    run_ids: list[str] = []
    baseline: Optional[str] = None
    study_id: Optional[str] = None
    sections: Optional[list[str]] = None
    images: bool = True
    notes: str = ""


def register(router: APIRouter) -> None:
    # ---------------------------------------------------------------- studies / optimisation
    @router.get("/studies")
    def studies(pid: str, e: EngineState = Depends(engine)):
        return svc.list_studies(e.projects.get(pid))

    @router.get("/studies/targets")
    def study_targets(pid: str, e: EngineState = Depends(engine)):
        return svc.targets(e.projects.get(pid))

    @router.post("/studies/preview")
    def study_preview(pid: str, body: StudyBody, e: EngineState = Depends(engine)):
        return svc.preview(e.projects.get(pid), e.ctx, body.kind, body.definition)

    @router.post("/studies")
    def study_start(pid: str, body: StudyBody, e: EngineState = Depends(engine)):
        with e.projects.lock(pid):
            return svc.start(e.projects.get(pid), e.ctx, body.kind, body.definition)

    @router.get("/studies/{sid}")
    def study_detail(pid: str, sid: str, e: EngineState = Depends(engine)):
        return svc.detail(e.projects.get(pid), sid)

    @router.post("/studies/{sid}/{action}")
    def study_action(pid: str, sid: str, action: str, e: EngineState = Depends(engine)):
        return svc.set_status(e.projects.get(pid), sid, action)

    @router.get("/studies/{sid}/export.csv")
    def study_csv(pid: str, sid: str, e: EngineState = Depends(engine)):
        return PlainTextResponse(svc.export_csv(e.projects.get(pid), sid), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{sid}.csv"'})

    @router.post("/studies/{sid}/designs/{design_id}/promote")
    def study_promote(pid: str, sid: str, design_id: str, e: EngineState = Depends(engine)):
        with e.projects.lock(pid):
            return svc.promote_design(e.projects.get(pid), sid, design_id)

    # ---------------------------------------------------------------- geometry parameters
    @router.get("/parameters")
    def parameters(pid: str, e: EngineState = Depends(engine)):
        p = e.projects.get(pid)
        return {"parameters": [x.model_dump(mode="json") for x in p.config.parameters], "values": p.config.parameter_values}

    @router.put("/parameters")
    def parameters_set(pid: str, body: ParamsBody, e: EngineState = Depends(engine)):
        with e.projects.lock(pid):
            return {"parameters": svc.set_parameters(e.projects.get(pid), body.parameters)}

    @router.post("/parameters/preview.abg")
    def parameters_preview(pid: str, body: ParamPreviewBody, e: EngineState = Depends(engine)):
        p = e.projects.get(pid)
        defs = None if body.parameters is None else svc.parse_parameters(body.parameters)
        header, blob = zsvc.morph_preview(p, p.config, body.values, defs, body.check)
        return Response(pack(header, blob), media_type="application/octet-stream")

    # ---------------------------------------------------------------- shape zones
    @router.get("/zones")
    def zones(pid: str, e: EngineState = Depends(engine)):
        p = e.projects.get(pid)
        return {"zones": [z.model_dump(mode="json") for z in p.config.zones],
                "parameters": [x.model_dump(mode="json") for x in p.config.parameters if x.deformation.kind == "zone"]}

    @router.get("/zones/presets")
    def zone_presets(pid: str, e: EngineState = Depends(engine)):
        return {"presets": zsvc.presets(e.projects.get(pid))}

    @router.put("/zones")
    def zones_set(pid: str, body: ZonesBody, e: EngineState = Depends(engine)):
        with e.projects.lock(pid):
            return zsvc.set_zones(e.projects.get(pid), body.zones)

    @router.post("/zones/freedom.abg")
    def zones_freedom(pid: str, body: ZonePreviewBody, e: EngineState = Depends(engine)):
        header, blob = zsvc.freedom_preview(e.projects.get(pid), body.zones)
        return Response(pack(header, blob), media_type="application/octet-stream")

    @router.post("/zones/preview.abg")
    def zones_preview(pid: str, body: ZonePreviewBody, e: EngineState = Depends(engine)):
        p = e.projects.get(pid)
        defs = zsvc.parameters_with_zones(p, body.zones) if body.zones is not None else None
        header, blob = zsvc.morph_preview(p, p.config, body.values, defs, body.check)
        return Response(pack(header, blob), media_type="application/octet-stream")

    @router.get("/studies/{sid}/designs/{design_id}/morph.abg")
    def design_morph(pid: str, sid: str, design_id: str, e: EngineState = Depends(engine)):
        header, blob = zsvc.design_morph(e.projects.get(pid), sid, design_id)
        return Response(pack(header, blob), media_type="application/octet-stream")

    # ---------------------------------------------------------------- reports
    @router.get("/reports")
    def reports(pid: str, e: EngineState = Depends(engine)):
        return rep.list_reports(e.projects.get(pid))

    @router.post("/reports")
    def report_create(pid: str, body: ReportBody, e: EngineState = Depends(engine)):
        return rep.create_report(e.projects.get(pid), e.ctx, body.model_dump(), viz=e.viz)

    @router.get("/reports/{rid}/{name}")
    def report_file(pid: str, rid: str, name: str, e: EngineState = Depends(engine)):
        path = rep.report_file(e.projects.get(pid), rid, name)
        media = {"html": "text/html", "csv": "text/csv", "png": "image/png", "json": "application/json"}.get(path.suffix[1:], "application/octet-stream")
        return FileResponse(path, media_type=media)
