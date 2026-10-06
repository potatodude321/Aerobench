"""Structured, user-facing error taxonomy (BUILD_SPEC §31).

Every error that can reach a user carries a plain-English summary, a likely cause, a
recommended action and the raw details.  Raw OpenFOAM output is never replaced by a
generic message — it is attached as ``details``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCategory(str, Enum):
    ENVIRONMENT = "environment"
    DEPENDENCY = "dependency"
    GEOMETRY = "geometry"
    MESH = "mesh"
    MPI = "mpi"
    SOLVER = "solver"
    DISK = "disk"
    MEMORY = "memory"
    VISUALISATION = "visualisation"
    PROJECT = "project"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Diagnostic(BaseModel):
    """A structured finding (error or warning) suitable for UI display."""

    code: str = Field(description="Stable machine-readable identifier, e.g. 'mesh.leak'")
    category: ErrorCategory
    severity: Severity = Severity.ERROR
    summary: str
    likely_cause: str = ""
    action: str = ""
    details: str = ""
    context: dict[str, Any] = Field(default_factory=dict)

    def short(self) -> str:
        return f"[{self.severity.value}] {self.code}: {self.summary}"


class AeroBenchError(Exception):
    """Exception carrying a :class:`Diagnostic`."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.summary)
        self.diagnostic = diagnostic

    @classmethod
    def make(
        cls,
        code: str,
        category: ErrorCategory,
        summary: str,
        likely_cause: str = "",
        action: str = "",
        details: str = "",
        **context: Any,
    ) -> "AeroBenchError":
        return cls(
            Diagnostic(
                code=code,
                category=category,
                summary=summary,
                likely_cause=likely_cause,
                action=action,
                details=details,
                context=context,
            )
        )


class ValidationIssue(BaseModel):
    """Configuration validation result entry (errors block generation, warnings don't)."""

    path: str
    severity: Severity
    message: str
    code: str = ""


def raise_if_errors(issues: list[ValidationIssue], what: str = "configuration") -> None:
    errors = [i for i in issues if i.severity == Severity.ERROR]
    if errors:
        text = "\n".join(f"- {i.path}: {i.message}" for i in errors)
        raise AeroBenchError.make(
            "config.invalid",
            ErrorCategory.CONFIGURATION,
            f"The {what} has {len(errors)} blocking problem(s).",
            likely_cause="Required inputs are missing or inconsistent.",
            action="Fix the listed items and try again.",
            details=text,
        )
