# Domain model

The project configuration (`engine/src/aerobench/domain/`) is the single source of truth
for what will be generated and run. It is a tree of Pydantic v2 models with strict types
(`extra = "forbid"`), units in field descriptions and engineering validation on top.

`project.json` holds one `ProjectConfig`; everything derived from it (geometry revisions,
meshes, runs, studies, reports) lives in the project database and folders and references the
configuration through **stage hashes**.

## ProjectConfig

| Field | Model | Meaning |
|---|---|---|
| `schema_version` | int | Migrated on load (`domain/migrations.py`) |
| `solver` | `SolverTarget` | Fork (`opencfd` / `foundation`) and version (default `v2606`) |
| `execution` | `ExecutionConfig` | Provider id, cores, decomposition (scotch/hierarchical/simple), write format, purge |
| `geometry` | `GeometryConfig` | Active geometry revision, source→CFD transform, components, summary, sizing box |
| `vehicle` | `VehicleConfig` | Category, wheels (position, centre, axis, rolling radius, rotation), ride-height offset, rake |
| `reference` | `ReferenceValues` | Area (computed frontal or manual), length, density, velocity, moment centre, axes; locked once results exist |
| `operating` | `OperatingCondition` | Speed U∞, yaw β, ground (moving/stationary), ground mode under yaw, wheel rotation, reference pressure |
| `tunnel` | `TunnelConfig` | Domain extents in vehicle lengths (preset or custom), symmetry, contact patch, far-field BCs, design yaw range |
| `mesh` | `MeshConfig` | Preset or custom: background cell, surface refinement per role/component, regions, layers, quality controls |
| `physics` | `PhysicsConfig` | Turbulence model, fluid (ρ, ν, temperature), inlet turbulence, wall treatment |
| `numerics` | `NumericsConfig` | Versioned preset, iterations, first-order start, averaging window, write interval, overrides |
| `observables` | `ObservablesConfig` | Component forces, y+, wall shear, Cp, vorticity, Q, continuity, probes |
| `expert` | `ExpertConfig` | Dictionary patches, whole-file overrides, extra function objects |
| `parameters` | `[GeometryParameter]` | Geometry parameters (rigid, FFD, RBF) available to studies |
| `studies`, `optimisations` | definitions | Saved study / optimisation definitions |

## Coordinates and units

SI throughout. CFD frame (ADR-0007): **+x downstream** (nose towards −x), **+z up**,
**+y to the vehicle's right**. `GeometryTransform` maps raw source coordinates to this frame:
`p_cfd = R · (s · p_src) + t`, where `R` sends the file's forward axis to −x and up axis to
+z; with `auto_place`, `t` puts the nose at x = 0, the lowest point at z = 0 and the
centreline at y = 0. Pose (ride height, rake) is applied after placement to body-attached
components only — wheels stay on the ground.

## Components and wheels

A geometry revision has named **regions** (STL solids, OBJ groups, CAD solids). Components
group regions and carry a **role** (body, wheel, wing, spoiler, splitter, diffuser, floor,
mirror, cooling inlet/outlet, stilt, other) which drives refinement and boundary conditions; each component becomes a patch.
Wheels are described explicitly (centre, axis, rolling radius) because their boundary
condition is a rotating-wall velocity `ω × r` with `ω = |U_ground| / R` (ADR-0008).

## Reference values

Coefficients are `C = F / (q A)` with `q = ½ ρ_ref U_ref²`; moments use the reference length
and the moment centre (default: ground point midway between the axles). The reference
area defaults to the computed projected frontal area (pixel method with an uncertainty
estimate). Once a run has results the reference is **locked**; changing it requires
confirmation, because coefficients computed with different references are not comparable.

## Stage hashes and staleness

`stage_hashes(cfg)` returns SHA-256 fingerprints of:

* **geometry** — revision, transform, components, pose, sizing box;
* **mesh** — geometry + wheels + tunnel (except far-field BC type) + effective yaw range +
  mesh settings + solver target + (for y+-targeted layers) speed and viscosity + generator version;
* **solve** — mesh + operating point + far-field BC + physics + numerics + observables + expert
  overrides + write format;
* **derived** — solve + reference values (except the lock flag).

A mesh or run is *up to date* when its stored hash equals the current one; the UI shows
"stale" badges otherwise. Geometry-parameter *definitions* are deliberately not part of the
hash: parameter values take effect by creating a new geometry revision.

## Geometry revisions

Revisions are immutable and content-addressed (`geometry/blobs/<sha256>.npz`). Kinds:
`baseline` (imported), `repaired`, `variant` (alternative import), `parameterised` (created by a
design; stored in the CFD frame with an identity transform and cached by a content key),
`optimised`, `replacement`. The revision record keeps its parent, operations and diagnostics.

## Design exploration

* `DesignVariable` — id, **target** (see `studies/targets.py`: `operating.speed`,
  `operating.yaw_deg`, `vehicle.ride_height_offset`, `vehicle.rake_deg`,
  `vehicle.front_ride_height`, `vehicle.rear_ride_height`, `mesh.base_cell_size`,
  `numerics.iterations`, `geometry.variant`, `param.<id>`), kind (continuous, integer,
  discrete), bounds or values.
* `StudyDefinition` — kind (one factor, full factorial, Latin hypercube, table), levels,
  samples, seed, include baseline, reported metrics, scheduling.
* `OptimisationDefinition` — variables, objectives (metric, sense, aggregate over
  conditions), constraints (`metric op value`, absolute or relative to the baseline),
  algorithm, budget, batch size, initial samples, seed, conditions, scheduling.
* `Scheduling` — concurrent designs, cores per run (product must not exceed the available
  cores), retries, whether unconverged runs count.
* `GeometryParameter` — id, name, units, bounds, baseline, affected and frozen components,
  deformation (`translate`, `rotate`, `ffd`, `rbf`), displacement limit, centreline symmetry.

* `ShapeZone` — id, name, preset, box, blend distance, parts, moves (`z`, `x` or `width`,
  largest movement in mm, 1–3 sections along the flow), centreline symmetry, enabled. Each
  enabled move creates geometry parameters `<zone>_<dir>[k]` (ADR-0013).

When a study starts, the definition and the **complete base configuration** are frozen in
the `studies` table, so later edits to the project never change a running study.

## Aero elements and the structural check

* `application` — `vehicle` or `aero_element` (a wing, flap or splitter studied alone).
* `ElementConfig` — environment (free stream or ground effect), ground clearance (m), angle
  (°, trailing edge up), height in chords for free stream. Applied through the vehicle pose
  (rake, ride-height offset) by `services/element.py`.
* `StructureConfig` — model (skin or plate, plate side), analysed parts, sections, supports,
  project materials, the run whose loads are applied, design speed, load factor,
  self-weight and gravity, required reserve factor, element limit.
* `Section` — sheet (material, thickness) or laminate (plies outer-first: material,
  thickness, angle; symmetric; 0° direction); `Support` — box, sphere or cylinder; `Material`
  — isotropic (E, ν, yield, ultimate, density) or lamina (E1, E2, G12, ν12, optional
  out-of-plane constants, Xt, Xc, Yt, Yc, S12, ply thickness, density).
* Structural checks are stored in the `structure_runs` table with a snapshot of the settings
  and materials used, the summary and the job (ADR-0012).

## Runs

A run record stores: frozen configuration, operating point and reference values, provider,
OpenFOAM fork/version, ParaView version, application version, hardware and environment
digest, generated-file manifest with hashes, resolved case summary (boundary conditions,
derived quantities, assumptions, warnings, expert overrides), timings, iterations,
metrics (window mean and last iteration), convergence assessment and diagnostics.
Reproducing a run creates a new run from the frozen configuration.

## Validation

`validate_project(cfg, stage)` returns `ValidationIssue`s (`path`, `severity`, `message`,
`code`). Errors block generation; warnings are shown. Current rules include: geometry
missing, unmeasured or without components; half-car symmetry combined with yaw (error);
wheels without surfaces, unknown wheel components, front/rear axle order; reference area,
length and moment centre set; turbulence-model notes (GEKO advanced, laminar unphysical);
target y+ inconsistent with the wall treatment; upstream/downstream extents below 1.5 / 3
vehicle lengths; parallel meshing fallbacks. Mesh-plan checks (blockage ratio above 2 %,
estimated cells and memory) are reported with the plan, and checkMesh/snappyHexMesh
findings with the mesh.
