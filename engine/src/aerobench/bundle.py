"""Components shipped inside the Windows installer (OpenFOAM, ParaView, MS-MPI installer).

Installed layout (``<install>\\resources``)::

    python\\python.exe                     embedded engine runtime
    openfoam\\OpenFOAM-v2606\\...           OpenCFD MinGW build (platforms\\win64MingwDPInt32Opt\\bin)
    ParaView-6.2.0\\bin\\pvpython.exe       ParaView (Kitware binaries, duplicates restored as hard links)
    redist\\msmpisetup.exe                  Microsoft MPI redistributable (installed by the setup)
    bundle.json                            what was bundled, versions and SHA-256 of the sources

The Electron shell passes the resources folder as ``AEROBENCH_BUNDLE_DIR``; the engine also
finds it from its own interpreter path, so the CLI (``resources\\python\\python.exe -m
aerobench.cli``) sees the same components.  Nothing here is required: a development
checkout simply has no bundle.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


def bundle_dir() -> Optional[Path]:
    env = os.environ.get("AEROBENCH_BUNDLE_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    try:
        cand = Path(sys.executable).resolve().parent.parent  # resources\python\python.exe -> resources
    except OSError:
        return None
    if (cand / "bundle.json").is_file():
        return cand
    return None


def manifest() -> dict[str, Any]:
    b = bundle_dir()
    if b is None or not (b / "bundle.json").is_file():
        return {}
    try:
        return json.loads((b / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def openfoam_root() -> Optional[Path]:
    b = bundle_dir()
    if b is None:
        return None
    root = b / "openfoam"
    return root if any(root.glob("OpenFOAM-v*")) else None


def pvpython() -> Optional[Path]:
    b = bundle_dir()
    if b is None:
        return None
    exe = "pvpython.exe" if os.name == "nt" else "pvpython"
    hits = sorted(b.glob(f"ParaView-*/bin/{exe}"), reverse=True)
    return hits[0] if hits else None


def msmpi_installer() -> Optional[Path]:
    b = bundle_dir()
    if b is None:
        return None
    p = b / "redist" / "msmpisetup.exe"
    return p if p.is_file() else None
