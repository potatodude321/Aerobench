"""Compare, studies, optimisation and report routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..services.compare import compare_runs
from .app import EngineState, engine

router = APIRouter(prefix="/api/p/{pid}")


class CompareBody(BaseModel):
    run_ids: list[str]
    baseline: Optional[str] = None
    metrics: Optional[list[str]] = None


@router.post("/compare")
def compare(pid: str, body: CompareBody, e: EngineState = Depends(engine)):
    return compare_runs(e.projects.get(pid), body.run_ids, body.baseline, body.metrics)


# Studies, optimisation, geometry parameters and reports
from .routes_studies import register as _register_studies  # noqa: E402

_register_studies(router)
