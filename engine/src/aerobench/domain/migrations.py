"""Project-schema migrations.

Each migration takes the raw JSON dict at schema ``n`` and returns it at ``n + 1``.
Migrations must be pure, deterministic and never drop user data silently: unknown data
that a migration cannot map is moved under ``_migrated_unmapped``.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ..core.errors import AeroBenchError, ErrorCategory

# Registry: from_version -> function producing from_version + 1
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migration(from_version: int):
    def deco(fn):
        MIGRATIONS[from_version] = fn
        return fn

    return deco


# Example of the pattern (schema 0 = pre-release prototype files without schema_version):
@migration(0)
def _v0_to_v1(d: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(d)
    d["schema_version"] = 1
    return d


def current_version() -> int:
    from .project import SCHEMA_VERSION

    return SCHEMA_VERSION


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    target = current_version()
    v = int(data.get("schema_version", 0))
    if v > target:
        raise AeroBenchError.make(
            "project.schema_newer",
            ErrorCategory.PROJECT,
            f"This project was saved by a newer version (schema {v}; this build supports {target}).",
            likely_cause="The project was opened in a newer release of the application.",
            action="Open it with that release, or export the data from it.",
        )
    while v < target:
        fn = MIGRATIONS.get(v)
        if fn is None:
            raise AeroBenchError.make(
                "project.migration_missing",
                ErrorCategory.PROJECT,
                f"No migration path from project schema {v}.",
                action="Report this project file to the development team.",
            )
        data = fn(data)
        v = int(data["schema_version"])
    return data
