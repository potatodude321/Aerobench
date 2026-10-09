# User guide

## 1. Install (Windows 10/11, 64-bit)

Run `AeroBench-Setup-<version>.exe` and accept the administrator prompt. That is all: the
setup installs everything the application needs.

| Included | What it is for |
|---|---|
| OpenFOAM v2606 for Windows (OpenCFD) | meshing and solving |
| Microsoft MPI 10.1.3 | needed by every OpenFOAM program of the Windows build, also for parallel runs; installed silently unless an MS-MPI is already present |
| ParaView 6.2.0 (Kitware) | field visualisation, report images, *Open in ParaView* |
| OpenCASCADE | STEP/IGES/BREP import |

Disk space: about 3.5 GB. A launch window shows progress while the engine starts. On the
start screen, *This computer* lists the components found (marked *included*), the cores and
memory. If Microsoft MPI is missing (for example removed later), it offers **Install
Microsoft MPI**.

Advanced alternatives (Settings → Solver): your own OpenFOAM v2606 installation, OpenFOAM
or a Linux workstation with `openfoam2606` installed. (WSL is no longer offered: the included
Windows build is the supported path.)

## 2. Create a project

Start screen → *New project*: name, folder and a starting point (Generic road car, Formula
Student, GT / race car, half-car symmetry, full-car yaw study, high-downforce, low-drag). The project is a folder you can copy, archive or back
up; it contains the configuration, the database, geometry revisions, meshes, runs and reports.

## 3. Geometry

* **Import** STL, OBJ, PLY, STEP, IGES or BREP (CAD import is part of the Windows installer;
  in a development environment it needs the `cad` extra). Units are detected
  from the size (a car in millimetres is recognised) and can be changed.
* **Orientation**: say where the nose points and which way is up *in the file*. The 3D view
  turns at once; wheels and reference values are recomputed in the background a moment
  later. Wheels are found from their shape (round, narrow, on a sideways axis, touching the
  ground) and placed front/rear and left/right from where they sit. If the file names a
  wheel differently from where it ends up (for example `wheel_FL` at the rear), the nose
  direction is probably wrong and AeroBench says so.
* **Diagnostics** list open edges, non-manifold edges, inverted normals, self-touching islands
  and tiny parts, with the affected regions. **Repair** runs a recorded pipeline (merge
  vertices, remove degenerate/duplicate faces, orient normals, fill small holes, remove
  islands) and creates a new revision; the original is kept.
* **Components**: regions are grouped into components with roles (body, wheel, wing,
  mirror, …). Wheels are detected automatically (centre, radius, axis) and can be edited.
  Each component becomes a separate patch with its own forces.
* **Geometry parameters** (Studies → Geometry parameters): define rigid moves, FFD lattices
  or RBF handles on chosen components, preview the deformed shape coloured by displacement
  and check validity.

## 4. Tunnel, mesh, physics

* **Tunnel**: domain presets in vehicle lengths with the resulting blockage ratio; moving or
  stationary ground; half model (zero yaw only); far-field handling under yaw; design yaw range
  so one mesh serves several yaw angles.
* **Mesh**: presets (fast, balanced, high accuracy) or custom refinement levels per
  component and region; boundary layers designed for a target y+ (wall functions or resolved);
  the panel shows the estimated cell count and memory before meshing. After meshing: cell
  count, checkMesh quality, layer coverage per patch and plain-language findings.
* **Inspecting the generated mesh** (Mesh → *Generated mesh* in the 3D view): the real cell
  faces on the vehicle and the ground, optionally as a wireframe, with the refinement boxes
  and the tunnel outline as they were for that mesh. *Cut through the volume* (lengthwise,
  across or level, with a position slider) shows the cells in a plane, coloured by
  refinement level with the same colours as the refinement boxes; the vehicle surface on
  the camera side is cut away so the cells around the body stay visible.
* **Physics**: speed, yaw, fluid properties (from temperature), turbulence model, inlet
  turbulence, numerics preset; the right-hand panel shows derived quantities (Re, q, y+ cell
  height, free-stream k/ω, wheel speeds) and the sign conventions.

## 5. Run

*Run → Run simulation* generates the case, meshes if no matching mesh exists, and solves in
a background process. You can close the application — the run continues; reopening the
project picks it up. Live residuals and force coefficients are shown with the averaging
window; the convergence table lists every criterion with its threshold. *Stop* writes the
current state and ends cleanly; *Resume* continues from it.

## 6. Results and comparison

* Key numbers: Cd, Cl, front/rear downforce, aero balance (shown as undefined when the vehicle makes net lift or near-zero downforce),
  side force and moments, CdA — with their sign conventions. Per-component forces, drag
  split into pressure and friction, y+ statistics.
* **Views**: Cp/y+/wall-shear maps, centreline and wake planes, underfloor, wheel wakes,
  streamlines, Q-criterion — computed by ParaView and shown in the 3D view; *Open in
  ParaView* for anything else. If a view has points without values (shown grey), a note says
  how much is missing.
* **Compare**: pick runs and a baseline; deltas (with percentages only where meaningful),
  warnings when runs are not comparable, side-by-side views with synchronised cameras.

## 7. Studies and optimisation

* **Studies**: choose variables (speed, yaw, ride height, rake, front/rear ride height,
  background cell size, iterations, geometry parameters, alternative geometries) and a
  design: one-factor sweep, full factorial, Latin hypercube or a table. *Preview* shows the
  designs and the number of runs; *Start* runs them in the background within the core budget.
  A yaw sweep uses one mesh; a sweep of the background cell size reports the grid
  convergence index.
* **Optimise**: objectives (e.g. minimise Cd, or Cd and Cl together), constraints (absolute
  or relative to the baseline, e.g. "Cl must not increase by more than 0.02"), algorithm
  (Bayesian for one objective, NSGA-II for trade-offs, Nelder–Mead for local refinement,
  space-filling for a survey), budget and conditions (e.g. average over 0° and 5° yaw).
  Failed or invalid designs are recorded with their reason and the optimiser learns from
  them. *Apply to project* makes a design the current configuration.

## 8. Reports

*Report* creates a self-contained HTML report (single run, comparison or study) with
results, convergence charts, ParaView images, setup and provenance, plus the data as CSV.
*Export PDF* prints it to A4. Reports state when results are not converged or not
comparable.

## 9. Expert Mode

Switch on *Expert* (top right) to see every generated dictionary, the exact commands, raw
logs, and to add overrides (dictionary patches, whole-file replacements, extra function
objects). Overrides survive regeneration, are validated, and appear in run provenance and
reports.

## 10. Troubleshooting

| Message | Meaning / action |
|---|---|
| Microsoft MPI is not installed | Click *Install Microsoft MPI* under *This computer* on the start screen (administrator approval) |
| No wheels found / "named as the FL wheel but sits at RR" | The nose or up direction is set wrongly: change it on the Geometry page |
| The OpenFOAM included with AeroBench is missing or damaged | Run the AeroBench setup again (repairs the installation; antivirus quarantine is the usual cause) |
| A required DLL was not found (STATUS_DLL_NOT_FOUND) | Usually missing MS-MPI; otherwise run the setup again |
| The meshing seed point is not inside the flow domain | Tunnel too small or geometry crossing the domain; enlarge the tunnel |
| The mesh leaked into the vehicle interior | The surface has openings larger than the local cell size; repair (fill holes) or refine |
| The solver diverged | Use the Robust numerics preset; check mesh quality and layer collapse |
| Parallel runs on Linux cannot use a folder with spaces | Save the project in a folder without spaces or run on one core |
| CAD file, no CAD tessellation component | Install the CAD component or export STL from the CAD system |
