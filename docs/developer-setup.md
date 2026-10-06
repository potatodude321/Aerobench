# Developer setup

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 – 3.13 | Engine; 3.12 recommended on Windows (embedded build used for packaging) |
| Node.js | 20 LTS or newer | Desktop shell (Electron 44, Vite 8, TypeScript 5.9) |
| Git | any | |
| OpenFOAM | OpenCFD v2606 | Windows installer, `openfoam2606` on Ubuntu/Debian, or inside WSL2 |
| Microsoft MPI | 10.x | **Windows only, required even for serial OpenFOAM runs** |
| ParaView | 6.2 | Optional for development; needed for visualisation tests |

## Engine

```bash
cd engine
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: . .venv/bin/activate
pip install -e .[dev]          # add ,cad for STEP/IGES import (OpenCASCADE via cadquery-ocp-novtk)
aerobench doctor               # solver / MPI / ParaView / hardware report
python -m pytest tests/unit    # fast, no external tools (~1 min)
python -m pytest tests/integration -m "openfoam"            # real OpenFOAM pipeline (~5–10 min)
python -m pytest tests/integration -m "paraview"            # ParaView sidecar
```

Useful environment variables:

| Variable | Effect |
|---|---|
| `AEROBENCH_CONFIG_DIR` | Settings folder (default: per-user application data). Tests set it to a temporary folder. |
| `AEROBENCH_PVPYTHON` | Explicit `pvpython` path |
| `AEROBENCH_GMSH` | Gmsh executable for CAD tessellation when OpenCASCADE is not installed |
| `AEROBENCH_LINK_DIR` | Folder for space-free case links on Linux |
| `AEROBENCH_UPDATE_GOLDEN=1` | Regenerate golden case files (review the diff!) |

Run the service on its own (for the browser or the Vite dev server):

```bash
aerobench serve --port 8765 --token devtoken --dev          # API at http://127.0.0.1:8765/api
aerobench serve --port 8765 --token devtoken --static ../desktop/dist   # serves the built UI too
# then open http://127.0.0.1:8765/?token=devtoken
```

## Desktop

```bash
cd desktop
npm ci
npm run typecheck
npm run build                  # renderer (dist/) + Electron main/preload (dist-electron/)
AEROBENCH_PYTHON=../engine/.venv/bin/python npx electron .
```

During UI work, run `aerobench serve --dev --port 8765 --token devtoken` and `npm run dev`;
open `http://localhost:5173/?engine=http://127.0.0.1:8765&token=devtoken`.

The Electron main process finds Python in this order: `AEROBENCH_PYTHON`, the bundled
interpreter (`resources/python`), `engine/.venv`, then `py -3` / `python3`.

## Repository conventions

* **Commits** are coherent units (feature + tests + docs where relevant).
* **Engine style:** type hints everywhere; Pydantic models for anything persisted; no shell
  strings for commands; errors that reach the user are `AeroBenchError` with a
  `Diagnostic` (code, category, summary, likely cause, action, details).
* **Generated dictionaries** change only together with their golden files.
* **Domain changes:** bump `SCHEMA_VERSION` and add a migration when a persisted field
  changes meaning or is removed; adding an optional field with a default needs no migration.
  Changing what feeds a stage hash invalidates existing meshes/runs — do it deliberately
  (and bump `GENERATOR_VERSION` when generated files change).
* **Database changes:** add a new numbered file in `engine/src/aerobench/project/sql/`.
* **Lint:** `ruff check src --select F,E9` must be clean.

## Test layout

```
engine/tests/
  unit/          pure Python: domain, AST, geometry, deformation, optimisers, studies (fake solver), API, GCI, CAD
  integration/   real OpenFOAM v2606 + ParaView 6.2 (markers: openfoam, paraview, slow)
  fixtures/      logs and postProcessing files captured from real runs
  golden/        generated case files per fork/version
```

Integration tests skip automatically when OpenFOAM or ParaView is not installed.
