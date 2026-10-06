"""Execution preferences and solver target."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field

from .common import Model


class Fork(str, Enum):
    OPENCFD = "opencfd"
    FOUNDATION = "foundation"


class SolverTarget(Model):
    fork: Fork = Fork.OPENCFD
    version: str = "v2606"


class DecompositionMethod(str, Enum):
    SCOTCH = "scotch"
    HIERARCHICAL = "hierarchical"
    SIMPLE = "simple"


class ExecutionConfig(Model):
    provider_id: Optional[str] = Field(None, description="None = application default provider")
    n_procs: int = Field(1, ge=1, le=4096)
    decomposition: DecompositionMethod = DecompositionMethod.SCOTCH
    write_format: str = Field("binary", pattern="^(binary|ascii)$")
    purge_write: int = Field(2, ge=0, description="Keep this many time directories (0 = all)")
