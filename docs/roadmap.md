# Roadmap

Status as of version 0.2.2 (2026-10). Everything listed under "Delivered" is implemented,
tested and documented; later items are ordered by engineering value.

## Delivered (0.1.0)

* Desktop shell (Electron/React) with Geometry, Tunnel, Mesh, Physics, Run, Results, Compare,
  Studies, Optimise, Report, Project, Logs, Expert and Settings pages; Windows installer.
* Typed, versioned project model with migrations, stage hashes and staleness tracking.
* Geometry import (STL/OBJ/PLY; STEP/IGES/BREP via OpenCASCADE or Gmsh), diagnostics, repair,
  components and wheel detection, frontal area, pose.
* Virtual wind tunnel, mesh planning (snappyHexMesh, layers for a target y+), steady RANS with
  moving ground, rotating wheels and yaw; versioned numerics presets.
* Execution providers: native Windows (v2606 + MS-MPI), Linux; detached crash-safe jobs.
* Results with explicit conventions, convergence assessment, ParaView 6.2 sidecar, compare.
* Studies, optimisation (GP-BO, NSGA-II, Nelder–Mead, space-filling), geometry
  parameterisation (rigid, FFD, RBF) with validity checks, GCI mesh sensitivity.
* Reports (HTML/CSV/PDF), validation benchmarks (Ahmed body), CLI.

## Delivered (0.2.0)

* Aero element projects (a wing, flap or splitter alone, in free stream or ground effect)
  and their **structural check** with CalculiX: loads from a run on the same surface, sheet
  and composite sections, built-in and user materials, supports, Tsai-Wu / maximum-stress
  ply failure, deflection and reserve-factor maps (ADR-0012).
* **Shape zones** for optimisation with movement heat maps and a baseline ↔ changed slider
  (ADR-0013); per-design shape view in optimisations.
* Ready-to-run demos (Ahmed body; front wing with its structural check) with guides.
* One-click optimisation goals (lower drag, more downforce, better efficiency) that choose
  variables, constraints, budget and scheduling (`services/quick_optimise.py`).
* Live logs for every job, cancellable meshing, time left and convergence criteria with the
  current values during runs, automatic stop once converged, results read and announced as
  soon as a run ends; no false "settings changed" states.
* The setup updates an installation in place, keeping unchanged components (ADR-0011). WSL
  removed: the bundled Windows build is the supported path.

## Delivered (0.2.1)

* Structural mesh remeshed for shells from production and CAD meshes (feature-preserving,
  curvature-adaptive, on the original surface); elements that would fold in CalculiX's
  expansion are isolated instead of the part being refused (ADR-0012).
* Optimisations on Windows run parallel designs one at a time (Microsoft MPI), which made
  every design after the first fail (ADR-0015).
* Shape zones shown on the parts as imported (where they act), with per-zone coverage, trial
  change diagnostics and aero element presets (ADR-0013).
* Bodies added from the same CAD model as parts or as modelled refinement volumes; boxes
  edited by dragging the arrows on their faces in the 3D view or field labels, mirrored
  copies, overlap notes and an overlap-aware cell estimate (ADR-0014).

## Delivered (0.2.2)

* First open-source release under the GNU GPL v3.0 or later, published on GitHub with the
  installer and the source code of the bundled GPL programs; About panel with the licence,
  trade mark notices and a link to the source; contributor guide, code of conduct, security
  policy and automatic checks on every change.

## Next (0.3)

1. **Validation records on Windows.** Run `aerobench benchmark` (Ahmed 25°/35°, medium and
   fine) on reference Windows workstations and publish the JSON records with each release.
   Development records ([benchmarks.md](benchmarks.md)) show the coarse level does not
   reproduce the 25°→35° drag drop and the medium 25° run oscillates without settling; if
   medium/fine do not capture the regime change, tune the automatic slant/wake refinement
   for separated rear ends (and offer a transient option, below) before claiming it.
2. **Remote / HPC provider transport.** SSH staging (rsync/SFTP), Slurm/PBS submission,
   queue monitoring and result retrieval; the provider interface and staging model exist.
3. **Run-time mesh adaptation of the wake** (refinement from a first solution) and automatic
   mesh-independence workflow (three levels + GCI in one action).
4. **Porous media** for radiators/condensers (Darcy–Forchheimer from pressure-loss curves)
   and cooling-drag decomposition.
5. **Wheels**: MRF for spoked rims and AMI sliding meshes as advanced options (schema already
   reserves `WheelModel.MRF`/`AMI`); tyre deformation/contact-patch shapes.
6. **Foundation adapter validation** against OpenFOAM Foundation v14 (currently experimental).

## Later

* Transient wakes (DES/IDDES with pimpleFoam), averaging and spectral post-processing.
* Adjoint shape sensitivities once the full chain (primal, adjoint, mesh morphing) is
  validated (ADR-0009).
* Surrogate models (GP/neural) trained on study databases with uncertainty, for interactive
  design-space exploration.
* Code-signed installers (for example through the SignPath Foundation, which signs
  open-source projects built from their public repository) and an update notice in the
  application.
* Building the Windows installer in GitHub Actions, so every release is built from the
  tagged source.
* Structural check: buckling, modal analysis (flutter margins) and progressive ply damage;
  solid inserts and bonded joints; loads from several runs combined into load cases.

## Known limitations

* Steady RANS only; strongly separated or unsteady flows carry model error, and their force
  coefficients may oscillate without settling (reported as such; window means are shown).
* The native Windows build cannot compile code at run time; features that would need
  `coded*` objects are not offered.
* Geometry parameters act on the triangulated surface (not on CAD); very large morphs can
  degrade triangle quality (warned).
* GCI is reported for studies that vary only the background cell size.
* Linux: parallel runs require project paths without spaces (OpenFOAM restriction).
* The structural check is linear static and shell-only with first-ply failure (ADR-0012).
  Separate bodies are separate pieces in the shell model (CalculiX cannot tie shells): each
  needs its own support.
