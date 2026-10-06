"""Expert-mode overrides that survive regeneration (ADR-0006)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from .common import Model


class DictPatch(Model):
    """Structured patch on a generated dictionary.

    ``key_path`` uses '/' as separator, e.g. ``SIMPLE/nNonOrthogonalCorrectors`` or
    ``relaxationFactors/equations/U``.  ``value`` is an AST-JSON value
    (see :func:`aerobench.foam.ast.from_json`) or a plain JSON scalar/list.
    """

    file: str = Field(description="Case-relative path, e.g. 'system/fvSolution'")
    key_path: str
    op: Literal["set", "delete"] = "set"
    value: Any = None
    note: str = ""
    stage: Literal["mesh", "solve"] = "solve"


class FileOverride(Model):
    file: str
    content: str
    note: str = ""
    stage: Literal["mesh", "solve"] = "solve"


class ExpertConfig(Model):
    patches: list[DictPatch] = Field(default_factory=list)
    file_overrides: list[FileOverride] = Field(default_factory=list)
    extra_function_objects: dict[str, str] = Field(
        default_factory=dict, description="name -> OpenFOAM dictionary body text (parsed and validated)"
    )
    notes: Optional[str] = None
