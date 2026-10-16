# ADR-0014 — Adding bodies, modelled refinement volumes and editing boxes in the view

Status: accepted (0.2.1)

## Context

A car or wing is often exported from CAD as several files: the body, then the wing, the
endplates, a splitter, each saved separately from the same assembly. Engineers also model
refinement volumes in CAD (a wake region, the gap under a wing) rather than typing box
coordinates. And boxes — refinement boxes, shape zones, structural supports — were edited
only by typing six coordinates, which is slow and error-prone when the aim is "a bit further
back" or "just covering the trailing edge".

## Decision

1. **Add bodies from the same model.** Further files are read with the transform of the
   geometry already imported (unit scale and axes; optionally other units for files exported
   differently) and the same translation. The placement translation is frozen (`auto_place`
   off), because re-placing the merged geometry would move what is already there (and the
   wheels, zones and boxes defined on it); changing units or axes later re-places everything.
   * *Parts* are merged into a new geometry revision (kind `added`, parent the previous one);
     each file — or each named solid in it — gets a unique region and component with the
     chosen role (not wheel or stilt: wheels are identified from the main geometry).
   * *Refinement volumes* must be closed (else refused: no inside to refine); they are
     oriented outwards and stored as a project blob in the source frame, referenced by a mesh
     region with a `surface` shape (bounds and volume kept for planning). When a mesh is made
     they are placed like the parts, posed with the body, written to `constant/triSurface`
     and refined `inside` by snappyHexMesh. The mesh stage hash includes the blob, so a
     changed volume makes the mesh out of date.
2. **Overlapping refinement volumes.** snappyHexMesh refines each cell to the finest level of
   the volumes containing it. The cell estimate now counts each point once at its finest
   level (each volume's share not inside a finer one, or an equally fine one listed before
   it, sampled on a grid over its own bounds), instead of adding the volumes. The editor tells
   the user where a finer box takes over and when a box lies entirely inside one at least as
   fine (it then changes nothing).
3. **Editing boxes in the 3D view.** A box editor overlays the viewer: clicking a box selects
   it; the selected box shows a handle on each face; dragging a handle moves that face along
   its axis (the closest point of the axis line to the mouse ray), in whole millimetres, never
   past the opposite face; symmetric zones keep their symmetry. The change is shown at once
   and stored when the handle is released. Number fields for box faces have draggable labels
   (Shift faster, Alt finer) with the same live preview. Boxes and zones can be mirrored
   across a plane normal to x, y or z at a chosen position (a copy).
   Used for your own refinement boxes (automatic ones keep their geometry; their level and
   switch can still be changed), shape zones and structural supports.

## Consequences

* Parts exported separately from one CAD assembly land where they belong without any
  manual alignment, and get their own forces.
* Refinement can follow shapes that boxes cannot, and the estimate stays honest when boxes
  overlap.
* The viewer registers its instances on `window.__abViewers` for automated interface tests
  (read-only use); the box editor hangs off the viewer it edits.
