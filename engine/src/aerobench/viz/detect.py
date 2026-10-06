"""Locate ParaView's pvpython (6.2 primary; any 6.x accepted, 5.12+ best effort)."""

from __future__ import annotations

import glob
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

PRIMARY = (6, 2)


def _candidates() -> list[str]:
    c: list[str] = []
    if os.environ.get("AEROBENCH_PVPYTHON"):
        c.append(os.environ["AEROBENCH_PVPYTHON"])
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            c += sorted(glob.glob(os.path.join(base, "ParaView*", "bin", "pvpython.exe")), reverse=True)
            c += sorted(glob.glob(os.path.join(base, "Programs", "ParaView*", "bin", "pvpython.exe")), reverse=True)
    else:
        c += sorted(glob.glob("/opt/paraview/*/bin/pvpython"), reverse=True)
        c += sorted(glob.glob("/opt/ParaView*/bin/pvpython"), reverse=True)
        c += sorted(glob.glob(os.path.expanduser("~/ParaView*/bin/pvpython")), reverse=True)
        c += sorted(glob.glob("/Applications/ParaView*.app/Contents/bin/pvpython"), reverse=True)
        for p in ("/usr/bin/pvpython", "/usr/local/bin/pvpython"):
            c.append(p)
    return [p for p in c if p and os.path.exists(p)]


def _version_from_path(p: str) -> Optional[tuple[int, int, int]]:
    m = re.search(r"ParaView[- ]?(\d+)\.(\d+)\.(\d+)", p)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def probe_version(pvpython: str, timeout: float = 90) -> Optional[tuple[int, int, int]]:
    code = ("from paraview import servermanager as sm;"
            "print('PVVERSION', sm.vtkSMProxyManager.GetVersionMajor(), sm.vtkSMProxyManager.GetVersionMinor(), sm.vtkSMProxyManager.GetVersionPatch())")
    try:
        out = subprocess.run([pvpython, "--force-offscreen-rendering", "-c", code], capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"PVVERSION (\d+) (\d+) (\d+)", out.stdout + out.stderr)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def detect_paraview(configured: Optional[str] = None, probe: bool = False) -> dict[str, Any]:
    cands = ([configured] if configured and os.path.exists(configured) else []) + _candidates()
    if not cands:
        return {"available": False, "pvpython": None, "version": None, "status": "missing",
                "message": "ParaView was not found. Install ParaView 6.2 from paraview.org (or set its path in Settings)."}
    best = None
    for p in cands:
        v = _version_from_path(p)
        if v is None and probe:
            v = probe_version(p)
        best = (p, v)
        if v and v[:2] == PRIMARY:
            break
    p, v = best
    status, msg = "ok", ""
    if v is None:
        status, msg = "unknown_version", "ParaView found; version will be verified on first use."
    elif v[0] < 5 or (v[0] == 5 and v[1] < 12):
        status, msg = "unsupported", f"ParaView {v[0]}.{v[1]} is too old; install ParaView 6.2."
    elif v[:2] != PRIMARY:
        status, msg = "supported", f"ParaView {v[0]}.{v[1]}.{v[2]} detected; 6.2 is the validated version."
    gui = None
    exe_dir = Path(p).parent
    for name in ("paraview.exe", "paraview"):
        if (exe_dir / name).exists():
            gui = str(exe_dir / name)
    return {"available": status != "unsupported", "pvpython": p, "paraview": gui,
            "version": ".".join(map(str, v)) if v else None, "status": status, "message": msg, "candidates": cands}
