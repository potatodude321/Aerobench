"""Job specification and state (file-based contract between engine and runner, ADR-0005)."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from ..core.errors import Diagnostic
from ..core.fs import atomic_write_text
from ..core.ids import utc_now_iso


class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"  # runner died without a terminal state (e.g. reboot)
    CANCELLED = "cancelled"  # never started

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.INTERRUPTED, JobStatus.CANCELLED)


class FoamStep(BaseModel):
    type: Literal["foam"] = "foam"
    name: str
    app: list[str]
    nprocs: int = 1
    optional: bool = False
    require_end: bool = True  # OpenFOAM prints "End" on success
    solver: bool = False  # supports graceful stop via the abort function object
    stage: str = ""  # mesh | solve | post (classification context)


class CopyStep(BaseModel):
    type: Literal["copy"] = "copy"
    name: str
    src: str
    dst: str
    optional: bool = False


class RemoveStep(BaseModel):
    type: Literal["remove"] = "remove"
    name: str
    globs: list[str]
    optional: bool = True


class DictSetStep(BaseModel):
    type: Literal["dict_set"] = "dict_set"
    name: str
    file: str
    key_path: str
    value: Any
    optional: bool = False


class StageStep(BaseModel):
    type: Literal["stage"] = "stage"
    name: str
    direction: Literal["in", "out"]
    optional: bool = False


class LinkMeshStep(BaseModel):
    """Hard-link (or copy) ``<src_case>/constant/polyMesh`` into this case."""

    type: Literal["link_mesh"] = "link_mesh"
    name: str
    src_case: str
    optional: bool = False


Step = Union[FoamStep, CopyStep, RemoveStep, DictSetStep, StageStep, LinkMeshStep]


class JobSpec(BaseModel):
    job_id: str
    kind: str  # mesh | solve | post | utility
    project_dir: str
    case_dir: str
    provider: dict[str, Any]
    steps: list[Annotated[Step, Field(discriminator="type")]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    cores: int = 1
    meta: dict[str, Any] = Field(default_factory=dict)
    resume_from_step: int = 0


class StepState(BaseModel):
    name: str
    status: str = "pending"  # pending | running | completed | failed | skipped | stopped
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    log: Optional[str] = None
    command: Optional[list[str]] = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class JobState(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    runner_pid: Optional[int] = None
    runner_create_time: Optional[float] = None
    child_pid: Optional[int] = None
    child_create_time: Optional[float] = None
    step_index: int = 0
    steps: list[StepState] = Field(default_factory=list)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    heartbeat: Optional[float] = None
    stop_requested: Optional[str] = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    message: str = ""


def write_model(path: Path, model: BaseModel) -> None:
    atomic_write_text(path, model.model_dump_json(indent=2))


def read_state(job_dir: Path) -> Optional[JobState]:
    p = Path(job_dir) / "state.json"
    if not p.exists():
        return None
    for _ in range(5):
        try:
            return JobState.model_validate_json(p.read_text())
        except (json.JSONDecodeError, ValueError, OSError):
            import time

            time.sleep(0.05)
    return None


def read_spec(job_dir: Path) -> JobSpec:
    return JobSpec.model_validate_json((Path(job_dir) / "job.json").read_text())
