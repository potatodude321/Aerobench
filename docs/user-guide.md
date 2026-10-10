# User guide

## 1. Install (Windows 10/11, 64-bit)

Run `AeroBench-Setup-<version>.exe` and accept the administrator prompt. That is all: the
setup installs everything the application needs.

| Included | What it is for |
|---|---|
| OpenFOAM v2606 for Windows (OpenCFD) | meshing and solving |
| Microsoft MPI 10.1.3 | needed by every OpenFOAM program of the Windows build, also for parallel runs; installed silently unless an MS-MPI is already present |
| ParaView 6.2.0 (Kitware) | field visualisation, report images, *Open in ParaView* |
| CalculiX 2.23 (G. Dhondt) | structural check of aero elements |
| OpenCASCADE | STEP/IGES/BREP import |

Disk space: about 3.6 GB. A launch window shows progress while the engine starts. On the
start screen, *This computer* lists the components found (marked *included*), the cores and
memory. If Microsoft MPI is missing (for example removed later), it offers **Install
Microsoft MPI**.

**Updating**: run the setup of the new version over the installed one. It recognises the
installation, replaces the application, and keeps each large component (Python runtime,
OpenFOAM, ParaView, CalculiX) that is unchanged in the new version, so an update takes a few
minutes instead of a full installation. Projects are never touched. `resources\setup.log`
in the installation folder records what was kept and what was installed.

Advanced alternatives (Settings → Solver): your own OpenFOAM v2606 installation, or a Linux
workstation with `openfoam2606` reached over SSH. (WSL is no longer offered: the included
Windows build is the supported path.)

## 2. Create a project

Start screen → *New project*: name, folder and a starting point (Generic road car, Formula
Student, GT / race car, half-car symmetry, full-car yaw study, high-downforce, low-drag, or
*Aero element* for a wing, flap or splitter studied on its own). The project is a folder you
can copy, archive or back up; it contains the configuration, the database, geometry
revisions, meshes, runs, structural checks and reports.

**Demos** (start screen → *Or try a demo*) need no files and are ready to run, with a short
guide above each page:

* *Ahmed body*: the classic reference body (measured Cd about 0.29), with two named
  refinement regions and shape zones over the rear slant and taper. A first run takes a few
  minutes and stops by itself once converged; then try a shape optimisation.
* *Front wing*: a wing 60 mm over a moving ground at 5°, with a carbon skin on two mounts
  for the structural check (section 7a).

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

* **Tunnel**: (aero elements: first where the element sits — in free stream, several chords
  above the ground, or over a moving ground at a given clearance — and its angle, trailing
  edge up; the pose follows at once and the clearance is kept when the angle changes)
  domain presets in vehicle lengths with the resulting blockage ratio; moving or
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
window and the estimated time left; the convergence table lists every criterion with its
threshold and the current value, and with *Stop when converged* the run ends by itself once
all of them are met. *Stop* writes the current state and ends cleanly; *Resume* continues
from it. A run waiting for its mesh shows as waiting and can be cancelled.

**Logs**: every job (meshing, each solver step, structural checks) has a live log. *View
the log* on the Mesh and Run pages, or the Logs page, follows the output as it is written.
Meshing can be cancelled from the Mesh page; runs that were waiting for that mesh are
cancelled with it and say why.

Results are read as soon as a run ends: a notification says so and the Results page opens on
the newest run.

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
* **One-click goals** (Optimise → *Start in one click*, or *Optimise* on the Results page):
  *Lower drag*, *More downforce* and *Better efficiency* (downforce ÷ drag) each start a
  complete optimisation of the current setup. Each goal shows, before you start, what may
  change, what must not get worse, how many designs it tries and roughly how long that takes
  (from your last run):
  * a vehicle changes shape inside its enabled shape zones; without any, the goal places
    typical ones (drag: rear taper, roof trailing edge, diffuser; downforce and efficiency:
    front splitter, rear deck, diffuser), which then appear under Shape zones;
  * an aero element changes its angle (±5°) and, over the ground, its height (half to twice
    today's), plus any enabled shape zones;
  * *Lower drag* lets lift rise by at most 0.02 in Cl; *More downforce* lets drag rise by at
    most 5 % (vehicles) or 10 % (elements) and, for a car that makes downforce, keeps the aero
    balance within 2 points; *Better efficiency* is offered when the vehicle makes downforce;
  * the search is Bayesian: today's shape first, a spread of designs, then designs chosen
    from what was learnt (about 4 × the number of variables, at most 30), several at once
    when the cores allow.
  The optimisation is then an ordinary one: it can be paused, its designs inspected and the
  best applied to the project.
* **Shape zones** (Optimise → Shape zones): boxes over the parts of the vehicle an
  optimisation may reshape — typical ones (front splitter, bonnet, roof tail, rear deck,
  diffuser, boat tail, side skirts) or your own. Each zone moves its part of the surface up or
  down, fore or aft, or wider or narrower, as a whole or in two or three sections, by at most
  the distance you set; everything outside the zones keeps its imported shape exactly, and
  inside the change fades out smoothly towards the zone's edges. *Where it can change*
  colours the surface by how free it is; *Trial change* moves the zone's sliders and shows
  the heat map of the movement (red outwards, blue inwards) with a slider that blends between
  the baseline and the changed shape. *Optimise these* starts an optimisation with the
  zones' variables, and each design in its table can be shown the same way.
* **Optimise**: objectives (e.g. minimise Cd, or Cd and Cl together), constraints (absolute
  or relative to the baseline, e.g. "Cl must not increase by more than 0.02"), algorithm
  (Bayesian for one objective, NSGA-II for trade-offs, Nelder–Mead for local refinement,
  space-filling for a survey), budget and conditions (e.g. average over 0° and 5° yaw).
  Failed or invalid designs are recorded with their reason and the optimiser learns from
  them. *Apply to project* makes a design the current configuration.

## 7a. Structural check (aero elements)

In an *Aero element* project the workflow continues with **Structure**: the loads of a
finished flow simulation are applied to the same surface as thin shells and CalculiX
computes deflection and stresses; every ply or sheet is checked for failure.

* **Loads**: the run whose loads are applied, optionally scaled to another speed (loads grow
  with the square of the speed) and by a load factor for kerbs, bumps and gusts; the part's
  own weight can be included. *Applied force* shows the total, and how close it is to the
  force OpenFOAM integrated (normally a fraction of a percent).
* **Model**: *Skin* — the whole outer surface is the shell, with the section's thickness
  inside it (hollow or foam-cored wings and flaps); *Plate* — only the upper or lower face
  carries a flat plate under it (splitters, endplates, floors). A coarse CAD tessellation is
  divided into smaller triangles on the surface itself (the shape does not change), up to
  the element limit.
* **Sections**: a sheet of one metal or plastic and its thickness, or a composite laminate:
  plies listed from the outer surface inwards, each with its material, thickness and angle
  from the 0° direction (along the span by default); *Mirror about the mid-plane* doubles a
  symmetric layup. Several sections can apply to different parts.
* **Materials**: 19 built-in materials (aluminium alloys, steels, titanium, printed and
  moulded plastics, carbon, glass, aramid and flax plies) with typical data-sheet values, and
  your own: *Materials → New material* (or copy a built-in one and change it). Plies are
  entered with their stiffnesses (E1, E2, G12, ν12) and strengths (Xt, Xc, Yt, Yc, S12);
  impossible elastic constants are refused. Your materials are kept on this computer for all
  projects.
* **Supports**: boxes where the part is bolted or bonded. A box that takes in a patch of the
  surface holds it like a flange; one that only touches a line of it clamps that line.
  Presets place both ends, two mounts under the element, one central mount, or the rear
  edge. Each support shows how much of the surface it holds.
* **Pass criterion**: the required reserve factor — how many times larger all loads could be
  before the first ply or the material fails (Tsai-Wu and maximum stress for plies, yield
  for metals and plastics).
* *Run the structural check* solves in the background (typically under a minute). The
  result shows the reserve factor on the surface (red below 1, amber below the requirement),
  the deflection with an exaggeration slider, and the stress; the summary names the weakest
  point — section, ply and failure mode (fibre or matrix, tension or compression, shear) —
  the largest deflection, the structure's mass and the check that the support reactions
  balance the loads. Each check keeps its own settings; the list shows them all with their
  logs.

Limits: linear statics (small deflections; a large one is flagged), shells (no solid
inserts or bonded joints), first-ply failure (no progressive damage), stresses at
integration points (peaks right at supports and sharp corners depend on the mesh).

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
| Structural check: "thicker than the tightest curves of the part allow" | The laminate does not fit inside a sharp leading or trailing edge; use a thinner section, the plate model, or a less sharp edge |
| Structural check: the structure can move freely | The supports do not hold the part in every direction; add or enlarge a support |
| Structural check: a support holds nothing | Move or enlarge its box so that it overlaps the part where it is attached |
