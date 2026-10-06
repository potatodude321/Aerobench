"""User-level application settings (not part of any project)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .core.fs import atomic_write_json


def config_dir() -> Path:
    if os.environ.get("AEROBENCH_CONFIG_DIR"):
        return Path(os.environ["AEROBENCH_CONFIG_DIR"])
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "AeroBench"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aerobench"


class ParaViewSettings(BaseModel):
    pvpython: Optional[str] = None  # None = auto-detect
    paraview: Optional[str] = None  # GUI executable for "Open in ParaView"


class AppSettings(BaseModel):
    providers: list[dict[str, Any]] = Field(default_factory=list)
    default_provider: Optional[str] = None
    max_cores: Optional[int] = None
    paraview: ParaViewSettings = Field(default_factory=ParaViewSettings)
    allow_experimental_adapters: bool = False
    recent_projects: list[str] = Field(default_factory=list)
    worker_idle_timeout_s: int = 300
    expert_mode: bool = False
    projects_dir: Optional[str] = None  # default parent folder for new projects

    def default_projects_dir(self) -> str:
        if self.projects_dir:
            return self.projects_dir
        home = Path.home()
        if os.name == "nt":
            docs = home / "Documents"
            return str((docs if docs.is_dir() else home) / "AeroBench Projects")
        return str(home / "AeroBench-Projects")

    @classmethod
    def load(cls) -> "AppSettings":
        p = config_dir() / "settings.json"
        if p.exists():
            try:
                return cls.model_validate(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — never block start-up on a bad settings file
                p.rename(p.with_suffix(".json.invalid"))
        return cls()

    def save(self) -> None:
        atomic_write_json(config_dir() / "settings.json", self.model_dump(mode="json"))

    def add_recent(self, path: str) -> None:
        self.recent_projects = [path] + [p for p in self.recent_projects if p != path][:14]
