# Architecture

AeroBench has three processes during normal use and one per running job:

```
┌──────────────────────────── Electron desktop shell ────────────────────────────┐
│ main process: starts the engine, native dialogs, PDF printing, opens ParaView  │
│ renderer (React): pages, 3D viewport (three.js), charts (ECharts)              │
└───────────────▲─────────────────────────────── HTTP + WebSocket (localhost,   ─┘
                │                                 per-launch bearer token)
┌───────────────┴──────────── Engine service (Python, FastAPI) ───────────────────┐
│ API ─► services ─► domain model ─► validation ─► case generation ─► jobs        │
│                 └► results/metrics, studies, reports, viz client                │
└───────▲───────────────────────▲──────────────────────────────▲──────────────────┘
        │ spawns (detached)     │ pvpython sidecar (JSON lines) │ reads/writes
┌───────┴────────┐   ┌──────────┴──────────┐        ┌──────────┴──────────┐
│ project worker │   │ ParaView 6.2 server │        │ project folder       │
│ (one/project)  │   │ (pvpython)          │        │ project.json, SQLite,│
└───────┬────────┘   └─────────────────────┘        │ geometry, meshes,    │
        │ launches (detached)                      │ runs, reports        │
┌───────┴────────┐                                  └─────────────────────┘
│ job runner     │──► OpenFOAM processes via an execution provider
│ (one per job)  │    (native Windows + MS-MPI, Linux, remote)
└────────────────┘
```

## The pipeline

Every user action follows the same path (BUILD_SPEC "pipeline"):

1. **UI** edits a typed configuration through the API (`PATCH /api/p/{pid}/config`).
2. **Domain model** (`domain/`, Pydantic v2) validates field types and ranges; the project
   configuration has a schema version and migrations (`domain/migrations.py`).
3. **Engineering validation** (`validate_project`) returns errors and warnings with paths
   (half model with yaw, missing wheels for rotating wheels, blockage, …).
4. **Stage hashes** (`stage_hashes`) fingerprint the inputs of geometry, mesh, solve and
   post-processing; staleness of existing meshes and runs is a hash comparison.
5. **Fork adapter** (`foam/adapters/`) maps neutral settings to OpenCFD v2606 (default) or
   OpenFOAM Foundation syntax.
6. **Case generation** (`foam/case/`) builds dictionaries as an AST (`foam/ast.py`) and writes
   them deterministically; expert overrides are applied to the AST and recorded as a diff.
7. **Execution provider** (`execution/`) turns `[app, args]` into a concrete command
   (argv + environment + working directory), never a shell string.
8. **Job runner** (`jobs/runner.py`) executes the steps of a job as a detached process
   with file-based state, heartbeats, a control file for stop requests and logs.
9. **Ingestion** (`results/`, `monitoring/`) parses `postProcessing`, reduces forces with
   the conventions of ADR-0007, assesses convergence and stores results in SQLite.
10. **Visualisation** (`viz/`) asks the ParaView sidecar for extracts (surfaces, slices,
    streamlines, iso-surfaces) as compact binary payloads for the in-app viewer, and for
    screenshots for reports.
11. **Compare / studies / reports** read only stored results.

## Processes and robustness

* **The engine** is stateless apart from open-project handles; it can be restarted at any
  time. It binds to `127.0.0.1` on a random port and requires a per-launch token.
* **Jobs survive the UI.** A job is a directory (`jobs/<id>/`) with `spec.json`,
  `state.json` (single writer: the runner), `control.json` (stop requests), step logs and an
  events log. The runner is started detached (new session / `DETACHED_PROCESS`), so closing
  the application, or a crash of the engine, does not stop a solve (ADR-0005).
* **Stopping cleanly.** A stop request makes the runner touch the trigger file of an
  `abort` function object (`aerobenchStop`, action `writeNow`) so OpenFOAM writes the
  current time and exits; a forced stop terminates the process tree.
* **One worker per project** holds a lock, launches queued jobs within the core budget,
  ingests finished jobs and drives studies. On project open the engine reconciles job
  states (lost runners become `interrupted`, never `completed`).
* **Persistence** (ADR-0004): `project.json` (atomic writes, autosave snapshots) + SQLite
  (WAL) for revisions, runs, jobs, studies, reports and events; numbered SQL migrations.

## Engine package map

| Package | Responsibility |
|---|---|
| `domain` | Project model, enums, validation, stage hashes, templates, migrations |
| `aero` | Free-stream direction, force/moment reduction, metric definitions and sign conventions |
| `foam` | Dictionary AST/parser/writer, fork adapters, case builder, log parsers and error classification |
| `geometry` | Surface IO, CAD tessellation, diagnostics, repair, components, frontal area, pose, deformation |
| `meshing` | Domain sizing, refinement regions, boundary-layer design |
| `execution` | Providers (native Windows, local POSIX, remote) and detection |
| `jobs` | Job specs, runner, manager, worker; the computer-wide MPI lock (one parallel job at a time where the MPI cannot share the computer: Microsoft MPI, ADR-0015) |
| `results`, `monitoring` | Ingestion and convergence assessment |
| `viz` | ParaView detection, sidecar client/server, presets, binary payloads |
| `studies`, `optimisation` | Design space, DOE, design evaluation, driver, ask/tell optimisers, GP |
| `structures` | Structural check of aero elements: shell mesh remeshed from the CFD surface (feature-preserving, curvature-adaptive, ADR-0012), load mapping, CalculiX deck and shell-expansion plan (folding elements isolated), results and failure criteria, material library |
| `validation` | Benchmarks, grid convergence index |
| `services` | Use cases called by the API (geometry, mesh, runs, viz, compare, studies, shape zones, aero element setup, structures, demos, reports) |
| `api` | FastAPI app, routes, WebSocket event stream, server entry point |

## Desktop

* **Electron main** (`desktop/electron/main.ts`) resolves a Python interpreter (bundled →
  project venv → system), starts `aerobench.api.server`, reads the `AEROBENCH_READY` line
  (port, token), and loads the renderer from the engine (same origin; no file:// pages).
  The preload exposes a minimal typed bridge (dialogs, open file/folder, open ParaView,
  print to PDF). Context isolation and the sandbox are on; Node is not available to pages.
* **Renderer** (`desktop/src`): React 19 + Mantine 9; zustand store; a typed API client
  with structured errors (`Diagnostic`); a WebSocket event stream for job progress and log
  tails; a three.js viewer for geometry, overlays (tunnel, refinement boxes, modelled
  refinement volumes, wheels) and ParaView extracts; a box editor on top of it (click a
  box, drag the arrow on a face) for refinement boxes, shape zones and structural supports,
  and number fields whose labels can be dragged (ADR-0014).

## Why ParaView runs out of process

ParaView owns VTK, its Python and its rendering; mixing it into the engine's interpreter
would pin the engine to ParaView's Python version and make crashes in VTK fatal to the
service. A long-lived `pvpython` sidecar per project/run speaks JSON lines and is restarted
automatically if it dies (ADR-0003). The engine never needs ParaView to compute forces —
only to visualise fields — so the core workflow works without it.

## Security model

* Engine on localhost only; random port; per-launch token required on every API and
  WebSocket request (`/api/health` excepted).
* No shell strings: commands are argv lists; file names from users are validated against
  path traversal where they address files inside the project.
* Generated cases never contain dynamic code (`#codeStream`, `coded*`, `#calc`); a
  generator check enforces this (the Windows build cannot compile code, and it removes a
  code-injection path through dictionaries).
* Expert overrides are explicit, stored, diffed and shown in reports.
