"""Execution-provider interface (ADR-0002)."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.errors import Diagnostic


@dataclass
class CommandSpec:
    """A fully resolved command: never a shell string."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # variables to set (merged onto the runner env)
    cwd: Optional[str] = None
    unset_env: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def display(self) -> str:
        """Human-readable command line (Expert Mode 'exact commands')."""
        import shlex

        return " ".join(shlex.quote(a) for a in self.argv)


@dataclass
class MPIInfo:
    available: bool = False
    kind: Optional[str] = None  # msmpi | openmpi | mpich | intel
    launcher: Optional[str] = None
    version: Optional[str] = None


@dataclass
class ProviderInfo:
    id: str
    kind: str
    label: str
    available: bool
    fork: Optional[str] = None
    version: Optional[str] = None
    project_dir: Optional[str] = None
    bin_dir: Optional[str] = None
    mpi: MPIInfo = field(default_factory=MPIInfo)
    dynamic_code: bool = False
    staging: str = "in_place"
    problems: list[Diagnostic] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["problems"] = [p.model_dump(mode="json") for p in self.problems]
        return d

    @property
    def blocking_problems(self) -> list[Diagnostic]:
        return [p for p in self.problems if p.severity.value == "error"]


class ExecutionProvider(ABC):
    kind: str = ""
    staging: str = "in_place"

    def __init__(self, provider_id: str, config: Optional[dict[str, Any]] = None):
        self.id = provider_id
        self.config = dict(config or {})
        self._info: Optional[ProviderInfo] = None

    # ------------------------------------------------------------------ detection
    @abstractmethod
    def detect(self) -> ProviderInfo:
        ...

    def info(self, refresh: bool = False) -> ProviderInfo:
        if self._info is None or refresh:
            self._info = self.detect()
        return self._info

    # ------------------------------------------------------------------ commands
    @abstractmethod
    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        """Command running ``app_argv`` (first item = OpenFOAM application) on ``case_dir``.

        ``nprocs > 1`` means a parallel run (``-parallel`` under MPI)."""

    def exec_path(self, host_path: Path) -> str:
        """Path of ``host_path`` as seen by the solver processes."""
        return str(host_path)

    def check_case(self, case_dir: Path, nprocs: int) -> list[Diagnostic]:
        """Provider-specific preconditions checked *before* a job is queued."""
        return []

    # ------------------------------------------------------------------ staging (staged providers)
    def stage_in(self, case_dir: Path) -> Optional[CommandSpec]:
        return None

    def stage_out(self, case_dir: Path) -> Optional[CommandSpec]:
        return None

    def serialise(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "config": self.config}

    def environment_digest(self) -> dict[str, Any]:
        info = self.info()
        return {
            "provider": self.id,
            "kind": self.kind,
            "fork": info.fork,
            "version": info.version,
            "project_dir": info.project_dir,
            "mpi": asdict(info.mpi),
        }


def which(name: str, path: Optional[str] = None) -> Optional[str]:
    import shutil

    return shutil.which(name, path=path)


def is_windows() -> bool:
    return os.name == "nt"
