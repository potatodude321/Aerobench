# AeroBench — automotive external-aerodynamics workbench

> **Working title.** "AeroBench" is a neutral placeholder name used only in
> `engine/src/aerobench/version.py` and the desktop brand strings; rename before release.

AeroBench is a Windows-first desktop application for vehicle external aerodynamics with
OpenFOAM. An engineer imports a vehicle, builds a virtual wind tunnel, meshes it with
snappyHexMesh, solves steady RANS with moving ground and rotating wheels, and explores the
results with ParaView — without editing dictionaries, and with every generated file, command
and number traceable. Expert Mode exposes everything underneath.

This is the **master build**: every feature is unlocked. There is no licensing, DRM,
feature gating or telemetry in the code base.

## What it does

| Area | Capabilities |
|---|---|
| Geometry | STL (ASCII multi-solid / binary), OBJ, PLY, STEP, IGES, BREP (OpenCASCADE tessellation, one region per solid); unit and orientation detection; diagnostics (open edges, non-manifold, inverted normals, islands); repair pipeline; automatic component and wheel identification; frontal area; ride height and rake |
| Wind tunnel | Domain presets in vehicle lengths with blockage ratio; moving or stationary ground; rotating wheels (rotating-wall velocity); yaw by rotated inflow with free-stream far field; half model; contact-patch treatment |
| Mesh | Automatic snappyHexMesh plan (surface, wake, underbody, wheel and front refinement regions); boundary layers sized for a target y+; parallel meshing; checkMesh and layer-coverage reporting with engineering findings |
| Physics & numerics | Steady incompressible RANS (k-ω SST, Spalart–Allmaras, realizable k-ε); versioned numerics presets (robust → high fidelity) with first-order start; derived quantities (Re, y+, inlet turbulence) shown before running |
| Execution | Native Windows OpenFOAM v2606 (MinGW + MS-MPI), WSL2, Linux; detached job runner (closing the app never kills a run); crash-safe resume; exact commands recorded |
| Results | Cd, Cl, axle loads, aero balance, side force, moments with explicit sign conventions; per-component forces; drag decomposition; convergence assessment (never "converged" unless the criteria pass) |
| Visualisation | ParaView 6.2 as the processing engine: Cp/y+/wall-shear maps, planes, streamlines, Q-criterion — rendered in the app; one click to open the case in ParaView; generated pvpython scripts |
| Compare | Deltas and percentages where meaningful, validity warnings (different reference values, unconverged runs), side-by-side 3D views with synced cameras |
| Studies | One-factor sweeps, full factorial, Latin hypercube, tables; yaw sweeps on a single mesh; mesh-sensitivity studies with grid convergence index |
| Optimisation | Bayesian optimisation (GP, constrained EI), NSGA-II (multi-objective), Nelder–Mead, space-filling; constraints absolute or relative to baseline; multi-condition objectives; failures as data; checkpoint/resume |
| Parameterisation | Rigid moves, FFD and RBF morphs of chosen components with validity checks (folds, self-intersection, enclosure, ground contact) |
| Reports | Self-contained HTML (charts, ParaView images, provenance), CSV, PDF export |

## Repository layout

```
engine/            Python engine (domain model, OpenFOAM case generation, execution, results, API)
  src/aerobench/
    domain/        typed, versioned project model (Pydantic) and validation
    foam/          dictionary AST/parser/writer, fork adapters, case builder, log parsers
    geometry/      surface IO, diagnostics, repair, components, pose, parameterisation
    meshing/       mesh planning and layer design
    execution/     providers: native Windows, WSL, local POSIX, remote (interface)
    jobs/          detached runner, job manager, per-project worker
    results/       force ingestion, metrics; monitoring/ convergence assessment
    viz/           ParaView sidecar client + server (pvpython), presets, binary payloads
    studies/       design space, DOE, design evaluation, study driver
    optimisation/  ask/tell optimisers, Gaussian process
    validation/    benchmarks (Ahmed body), grid convergence index
    services/      application services used by the API
    api/           FastAPI service (localhost, per-launch token, WebSocket events)
  tests/           unit, integration (real OpenFOAM/ParaView), fixtures from real runs
desktop/           Electron + React + TypeScript shell (Mantine UI, three.js viewport, ECharts)
docs/              architecture, domain model, compatibility, validation, ADRs, guides
packaging/         Windows build scripts (bundled OpenFOAM/ParaView/MS-MPI, embedded Python, electron-builder)
```

## Getting started

**Users (Windows 10/11, 64-bit):** run `AeroBench-Setup-<version>.exe` — one installer with
everything included: OpenFOAM v2606 for Windows, Microsoft MPI 10.1.3 (installed silently)
and ParaView 6.2.0. No other downloads or settings. See [docs/user-guide.md](docs/user-guide.md).

**Developers:** see [docs/developer-setup.md](docs/developer-setup.md).

```bash
# engine
cd engine && python -m venv .venv && . .venv/bin/activate && pip install -e .[dev]
python -m pytest tests/unit            # fast tests, no OpenFOAM needed
python -m pytest tests/integration     # real OpenFOAM v2606 + ParaView pipeline
aerobench doctor                        # environment check

# desktop
cd desktop && npm ci && npm run build && npx electron .
```

## Documentation

* [Architecture](docs/architecture.md) and [ADRs](docs/adr/)
* [Domain model](docs/domain-model.md)
* [OpenFOAM compatibility](docs/openfoam-compatibility.md)
* [ParaView integration](docs/paraview-integration.md)
* [Validation](docs/validation.md) and [benchmarks](docs/benchmarks.md)
* [User guide](docs/user-guide.md), [developer setup](docs/developer-setup.md), [packaging](docs/packaging.md)
* [Third-party components and data](docs/third-party.md)
* [Roadmap](docs/roadmap.md)

## Principles

1. **Never fake results.** Every number comes from stored solver output; missing data is shown
   as missing; unconverged and diverged runs are labelled as such everywhere, including reports.
2. **Traceability.** Each run stores its frozen configuration, generated files, exact commands,
   solver and ParaView versions, hardware and environment digest; any run can be reproduced.
3. **Engineering defaults, expert access.** Presets are versioned and explained; Expert Mode
   shows and overrides every generated dictionary with a recorded diff.
4. **Explicit conventions.** Coordinates and force/moment signs are defined once
   ([ADR-0007](docs/adr/0007-coordinates-and-sign-conventions.md)) and printed with results.

## Licence

Proprietary — all rights reserved (see `engine/pyproject.toml`). OpenFOAM, ParaView and MPI
are separate programs installed by the user and invoked as external processes; see
[docs/third-party.md](docs/third-party.md).
