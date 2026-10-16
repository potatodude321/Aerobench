# ADR-0013 — Shape zones: local morphing for optimisation

Status: accepted (0.2.0); item 6 added in 0.2.1

## Context

Geometry parameters (FFD lattices, RBF handles, rigid moves) can move any part of the
vehicle, but setting them up takes CFD expertise, and a careless lattice morphs the whole
car. Commercial tools for this job (adjoint- or morphing-based optimisation in AirShaper,
AOX-style workflows) let the user mark *where* the shape may change, and show the change as
a colour map of how far the surface moves. That is what an engineer needs to judge a design:
which parts moved, by how much, and whether the change is buildable.

## Decision

1. **A zone is a box** over part of the vehicle with: the parts it may move (wheels and
   stilts never), how it may move — up/down (z), fore/aft (x), wider/narrower (lateral
   scaling about the centreline) — and the largest movement in millimetres, as a whole or in
   two or three sections along the flow. Typical zones come as presets placed from the
   vehicle's extent (front splitter, bonnet, roof tail, rear deck, diffuser, boat tail, side
   skirts).
2. **Exactly nothing changes outside a zone.** Inside, the movement is weighted by a window
   that is 1 in the core and falls to 0 at the box faces with a C²-continuous smootherstep
   over a blend distance, so the surface stays smooth (no kinks in curvature at the zone's
   edge). Sections are a smooth partition of unity along x, so neighbouring sections never
   tear the surface apart.
3. **Zones generate geometry parameters** (`<zone>_<dir>[k]`, millimetres) that the existing
   optimisation and study machinery uses unchanged; editing zones does not change the stage
   hashes of the baseline, so existing meshes and runs stay current until a design is run.
4. **Views:** *Where it can change* colours the surface by the zone window (0–100 %); *Trial
   change* and every design of an optimisation show the movement normal to the surface
   (red outwards, blue inwards, grey unchanged) with a slider blending the baseline into the
   changed shape, the share of the surface that moves and the validity check (folds,
   intersections, ground contact).

5. **One-click goals** build on zones: a goal uses the enabled zones, or places typical ones
   for its purpose when there are none (one variable each), adds the constraints that keep
   the goal honest (lift for a drag search, drag and balance for a downforce search) and a
   Bayesian budget of max(5, 2d + 1) + max(6, 2d) + 1 designs (at most 30). Aero elements vary
   their angle and height over the ground instead.

6. **Zones act on the parts as imported (0.2.1).** The deformation is applied in the CFD
   frame *before* the pose (ride height, rake; an element's angle and height). The zone
   editor and the parameter editor therefore show the parts unposed, with the boxes where they
   act; showing them posed (0.2.0) put the boxes beside an angled element, and trial changes
   of boxes placed over what was shown moved nothing ("0 mm"). Each zone reports the share of
   the surface its box covers (zero: "misses the part"); a trial change reports the largest
   movement and says when nothing moves or when the movement is mostly tangential. Aero
   elements get their own presets (leading edge, trailing edge, mid-chord camber, chord
   length, tips); their boxes reach past the element above, below and beyond its edges by
   more than the fade-out, so upper and lower surfaces move together and only the chordwise
   ends fade. Sliver triangles of the tessellation that turn over under the change are a
   warning, not a fold. Zones whose boxes overlap (on the same parts) are flagged: their
   movements add up there.

## Consequences

* An optimisation over zones cannot produce a change the user did not allow, and its
  designs can be inspected one by one before anything is built.
* The Ahmed body demo ships with zones over the rear slant and taper so the workflow can be
  tried at once.
