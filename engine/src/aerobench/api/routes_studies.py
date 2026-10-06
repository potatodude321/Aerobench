"""Studies, optimisation, geometry parameters and reports (registered on the explore router)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from ..services import reports as rep
from ..services import studies as svc
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
        header, blob = svc.parameter_preview(e.projects.get(pid), body.values, body.parameters, body.check)
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
