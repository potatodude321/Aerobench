"""Identifiers and timestamps."""

from __future__ import annotations

import datetime as _dt
import secrets
import uuid


def new_uuid() -> str:
    return str(uuid.uuid4())


def short_id(prefix: str) -> str:
    """Sortable, human-friendly id: ``<prefix>-YYYYmmdd-HHMMSS-<6 hex>``."""
    now = _dt.datetime.now(_dt.timezone.utc)
    return f"{prefix}-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


_ID_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def check_id(value: str, what: str = "identifier") -> str:
    """Validate an id that becomes part of a file path (run, job, mesh, study, report ids)."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        from .errors import AeroBenchError, ErrorCategory

        raise AeroBenchError.make("request.bad_id", ErrorCategory.PROJECT, f"Invalid {what}.")
    return value
