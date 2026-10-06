# Validation and verification

AeroBench separates three questions:

1. **Is the software doing what it says?** (code and generator verification)
2. **Is a given result numerically sound?** (solution verification: iterative convergence,
   mesh sensitivity)
3. **Does the method predict reality?** (validation against measurements)

A green test suite answers only the first. The application never presents a number as more
certain than these checks justify: convergence status, mesh-sensitivity results and
validation records are carried into the results views, comparisons and reports.

## The ladder

| Level | Question | How it is checked | Where |
|---|---|---|---|
| L0 | Reductions, conventions, algorithms | Unit tests: force/moment reduction and sign conventions, axle loads and balance, free-stream direction under yaw, dictionary AST round trips, geometry operations and repair, deformation and validity checks, optimisers (incl. exact checkpoint/resume), GCI against the published example | `engine/tests/unit` |
| L1 | Generated cases are valid OpenFOAM input | Golden files, no-dynamic-code check, parsing of every generated dictionary, ~99 % identical round trip of the OpenFOAM v2606 tutorial dictionaries | `engine/tests/unit`, `tests/golden` |
| L2 | The pipeline is consistent end to end | Real OpenFOAM v2606 + ParaView 6.2 on a synthetic vehicle: parallel snappyHexMesh with layers, checkMesh, potentialFoam, simpleFoam, reconstruction; AeroBench's Cd/Cl/axle loads equal OpenFOAM's `forceCoeffs`; per-component forces sum to the total; results survive close/reopen; staleness after edits; exact commands recorded; yaw sweep shares one mesh and the side force has the documented sign; parameterised geometry runs through snappyHexMesh; reports with ParaView images | `engine/tests/integration` |
| L3 | Canonical validation | Ahmed body 25° (quantitative band from two experiments) and 35° (regime change), run with `aerobench benchmark` | [benchmarks.md](benchmarks.md) |
| L4 | Solution verification for a specific project | Convergence criteria on every run; mesh-sensitivity studies with the grid convergence index | below |
| L5 | Application validation | Customer vehicles or public automotive models (e.g. DrivAer, user-supplied) against wind-tunnel data | user workflow |

## Iterative convergence (every run)

A run is never declared converged on residuals alone (criteria version 1, reported with
every assessment):

| Indicator | Converged | Bounded oscillation |
|---|---|---|
| Cd: standard deviation / linear drift over the averaging window | ≤ 0.002 / ≤ 0.002 | ≤ 0.010 / ≤ 0.004 |
| Cl, Cl_front, Cl_rear: std / drift | ≤ 0.005 / ≤ 0.005 | ≤ 0.020 / ≤ 0.008 |
| Residuals (p, U): reduction from the peak | ≥ 2.5 orders or below 1e-4 | — |
| Continuity (max local error over the window) | ≤ 1e-3 | — |

* The averaging window is the final fraction of iterations set in Numerics (default 20 %,
  at least 50 iterations); reported coefficients are window means with the last-iteration
  values alongside.
* Status values: `converged`, `converged_oscillating` (criteria met within the bounded
  band), `not_converged` (with the drifting quantities named), `diverged` (NaN, exploding
  residuals or absurd coefficients), `insufficient_data` (fewer than 100 iterations).
* Unconverged runs are labelled in every table, chart and report; studies can be told to
  treat them as failures.

## Mesh sensitivity (per project)

Run a study on **Background cell size** (`mesh.base_cell_size`) with three or more levels,
refinement ratio ≥ 1.3 recommended. The study summary and report then show, for every
reported metric, the grid convergence index following Celik et al., *J. Fluids Eng.* 130
(2008) 078001:

* observed order *p* (fixed-point solution of the three-grid equations),
* Richardson-extrapolated value,
* fine-grid GCI with safety factor 1.25,
* asymptotic-range ratio (≈ 1 when the meshes are in the asymptotic range),
* explicit `oscillatory` / `divergent` / `insufficient` outcomes instead of a misleading
  number.

The implementation reproduces the published worked example (p = 1.53, φ_ext = 6.1685,
GCI = 2.17 %).

## Reference values and comparability

* Coefficients of different runs are comparable only with identical reference area,
  length and moment centre; the comparison view and reports warn otherwise.
* Design studies keep the baseline's reference values for every design so coefficient
  differences reflect forces, not reference changes; the design's own frontal area is
  available separately.
* Percent differences are not shown for quantities that change sign (lift, balance,
  moments, side force).

## What is not claimed

* No validation is claimed for configurations outside the benchmarks run on the user's own
  installation; the benchmark JSON records are the evidence.
* Steady RANS is a model: separated, unsteady wakes (e.g. bluff rear ends, 25° Ahmed slant)
  carry model error that no mesh refinement removes.
* MRF/sliding-mesh wheels, transient (DES/LES) and compressible flows are not generated in
  this release (see the roadmap).
