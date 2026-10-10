"""Locate the CalculiX solver (ccx): the copy shipped with AeroBench first, then a path set
in the environment (AEROBENCH_CCX) or found on PATH."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Optional


def _candidates() -> list[str]:
    from .. import bundle

    c: list[str] = []
    if os.environ.get("AEROBENCH_CCX"):
        c.append(os.environ["AEROBENCH_CCX"])
    b = bundle.calculix()
    if b is not None:
        c.append(str(b))
    for name in ("ccx", "ccx_2.23", "ccx_2.22", "ccx_2.21", "ccx.exe"):
        w = shutil.which(name)
        if w:
            c.append(w)
    return [p for p in c if p and os.path.isfile(p)]


def probe(exe: str, timeout: float = 30) -> Optional[str]:
    try:
        out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"Version\s+(\d+\.\d+)", out.stdout + out.stderr)
    return m.group(1) if m else None


@lru_cache(maxsize=4)
def _detect(configured: Optional[str]) -> dict[str, Any]:
    cands = ([configured] if configured and os.path.isfile(configured) else []) + _candidates()
    for exe in cands:
        v = probe(exe)
        if v:
            return {"available": True, "ccx": exe, "version": v, "message": f"CalculiX {v}"}
    return {"available": False, "ccx": None, "version": None,
            "message": "The CalculiX solver was not found. It is part of the AeroBench setup on Windows; elsewhere install "
                       "CalculiX (ccx) and make it available on PATH or set AEROBENCH_CCX."}


def detect_calculix(configured: Optional[str] = None, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        _detect.cache_clear()
    return dict(_detect(configured))
