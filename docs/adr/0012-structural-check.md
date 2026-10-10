# ADR-0012 — Structural check of aero elements with CalculiX

Status: accepted (0.2.0)

## Context

Wings, flaps and splitters are designed for their loads as much as for their aerodynamics:
a layup that is too thin fails or flutters, one that is too thick costs weight. The loads
are exactly what the flow simulation computes. The workflow asked for is: aerodynamics →
the forces from OpenFOAM → applied to the same surface in a finite-element model → checked
for failure, with metals, plastics and composite layups, built-in materials and the user's
own.

## Decision

1. **Aero element projects.** A template (`aero_element`) studies one element alone: in free
   stream (several chords above a moving ground) or in ground effect at a given clearance,
   at a given angle; the pose is applied through the existing vehicle pose (rake, ride
   height), wheels are never identified, and the reference area is the planform. These
   projects get a seventh workflow step, *Structure*.
2. **Solver: CalculiX** (ccx 2.23, GPL-2.0-or-later), run as a separate program like
   OpenFOAM (no linking). The Windows build is conda-forge's, pinned by SHA-256 with every
   runtime DLL; it is installed as an updatable component (ADR-0011). On Linux the system
   `ccx` is used. It was chosen for its composite shell sections with per-ply materials and
   orientations, a Windows build that runs from a folder without installation, and its size
   (55 MB with its DLLs).
3. **Same surface.** The structural mesh is the CFD surface of the analysed parts, posed
   exactly as in the run. CAD tessellations are long slivers; the quadratic S6 shell
   tolerates elongated elements (a cantilever of 125:1 slivers deflects within 10 % of beam
   theory) but must be short along the direction the part bends. The surface is therefore
   divided without changing its shape (`structures/refine.py`): edges longer than the target
   size are divided into equal segments and each face (or a flat quad of two faces across
   their longest edge) is re-triangulated in its own plane from its divided boundary;
   Delaunay flips only where the surface moves less than a CAD tolerance. The target comes
   from the element budget (default 30 000). Meshing anew (gmsh was tried) was rejected: it
   moves the surface, loses area at sharp edges, takes 30 s and would add a GPL program.
4. **Loads.** Per CFD wall face F = ρ(p − p_ref)S − ρτ_w|S| (OpenFOAM's `forces`
   convention, verified against it). The wall traction is sampled over every structural
   triangle (an n × n subdivision chosen from the size ratio) taking the nearest flow face on
   the same side of the part, and each sample is shared between the six nodes with the S6
   shape functions — consistent loads that keep force and moment of each sample. The small
   sampling remainder is spread as a uniform traction, so the applied total equals the
   integrated wall force exactly; it is checked against OpenFOAM's force integral and
   reported. Loads scale with (design speed / run speed)² and a load factor. Self-weight is
   written as consistent nodal loads too (a third on each mid-edge node), so the deck knows
   every force; forces on supported nodes go straight into the supports and are reported.
5. **Sections.** A sheet (isotropic material, thickness) or a laminate (plies outer-first:
   material, thickness, angle from a 0° direction; optional symmetry). CalculiX stacks layers
   from the negative-normal side, so plies are written inner-first and outward normals are
   enforced on closed parts. Ply orientations are built per normal-direction bin (about 10°
   wide) from the section's 0° direction projected onto the surface. Skin model: the shell
   lies inside the outer surface (OFFSET 0.5). Plate model: only the upper or lower faces
   carry a plate inside the part.
6. **Expansion check before solving.** CalculiX expands shells into 3D wedges along nodal
   normals; a thick section on a tight curve folds over ("nonpositive jacobian").
   `structures/expansion.py` reproduces its normal grouping (gen3dnor: 20° groups in element
   order) and evaluates the wedge Jacobian on a grid denser than the integration points. A
   section that does not fit inside is centred on the surface (OFFSET 0) with a note; one that
   does not fit even centred is refused with the place named.
7. **Supports.** Boxes (sphere, cylinder possible). A support that contains whole elements
   (a patch) holds translations only: the patch is clamped through those elements and the
   reaction totals stay exact. A support touching only a line or points clamps rotations too
   (otherwise a hinge); CalculiX passes part of those reactions through generated rotation
   constraints and leaves them out of its totals, so the reaction check is skipped then.
8. **Failure.** Linear static analysis. Metals and plastics: von Mises against yield.
   Plies: stresses in ply axes at every integration point (6 per layer), Tsai-Wu (F12 = −½
   √(F11F22)) and maximum stress, the smaller reserve factor governs and the maximum-stress
   mode names it (fibre/matrix, tension/compression, shear). Result: the smallest reserve
   factor against a required one (1.5 by default), the critical section, ply and mode, the
   largest deflection, the mass, the reaction balance; a deflection above 20 % of the part
   is flagged as outside linear theory.
9. **Materials.** 19 built-in materials with typical data-sheet values and their sources; a
   user library in the settings folder (`materials.json`) for all projects; project-level
   materials in the configuration. Laminae are validated (positive-definite stiffness).
10. **Jobs.** A check is a job (kind `fea`) with a program step and a success marker
    ("Job finished"), its own log and cancellation like any other job; results are read by
    the worker and stored with a snapshot of the settings and materials used.

## Consequences

* Unit tests compare CalculiX with beam theory for a sheet and a unidirectional laminate
  cantilever (deflection, failing ply and mode), check load mapping in both size regimes,
  the expansion check, the ply order and orientations, patch/edge supports and self-weight.
* First-ply failure only (no progressive damage, buckling or fatigue), shells only (no
  inserts or bonded joints); stresses at supports and sharp corners are mesh dependent.
  These limits are stated in the user guide.
* A structural check takes under a minute for 30 000 elements on a laptop.
