# ADR-0003 — ParaView integration via a pvpython sidecar

Status: Accepted (2026-10-06)

## Context

We want ParaView power without ParaView complexity. ParaView's Python (`pvpython`)
ships its own interpreter (Python 3.12 for ParaView 6.2), so `paraview.simple` cannot be
imported into the engine's Python. Options: (1) embed stock ParaView GUI — rejected
(MASTER §6); (2) reimplement processing in JS — rejected (MASTER §6); (3) pvserver
client/server with a custom thin client — heavy, requires a VTK/ParaView client build;
(4) sidecar process running our script under `pvpython`.

## Decision

**Sidecar (4), hybrid rendering.**

* The engine launches `pvpython --force-offscreen-rendering aerobench_pv_server.py`
  per visualisation session. Protocol: newline-delimited JSON request/response over the
  child's stdin/stdout (no network port at all; stderr is logged). Each request has an
  id, op and params; responses carry results or a structured error with traceback.
* ParaView owns the pipeline: `OpenFOAMReader` (on a `.foam` stub), `ExtractBlock`,
  `Slice`, `Clip`, `Contour`, `StreamTracer`, `Gradient` (vorticity, Q-criterion),
  `Calculator` (Cp, |U|/U∞), `PointDatatoCellData`/`CellDatatoPointData`, `Decimate`,
  `PlotOverLine`, `ProbeLocation`.
* Outputs:
  * **Interactive geometry** — triangulated surfaces / polylines with one scalar (and
    optional vectors) written to a compact binary file (`.abg`: JSON header + little
    endian float32/uint32 blocks) in the project cache, served to the renderer and drawn
    with three.js using the same colour maps. Large outputs are decimated with
    ParaView's `Decimate` (LOD) and the decimation ratio is reported.
  * **High-fidelity images** — ParaView `SaveScreenshot` for reports and thumbnails.
  * **Data** — arrays, ranges, line samples, probes as JSON.
* Automotive presets (centreline Cp, wake plane, underfloor, wheel wakes, Cp/y+ body
  maps, streamlines, Q iso-surface) are declarative pipeline specs that expand to normal
  ParaView filters; they can be exported as a `pvpython` script for Expert Mode.
* Scene state (camera, field, range, colour map, filters, slice positions, visibility) is
  stored in the project DB, not in ParaView state files, so scenes can be re-applied to
  another run for comparison.
* Version: 6.2 is primary; any 6.x is accepted with a warning; 5.12+ is best-effort.

## Consequences

* No ParaView in the engine process: crashes in ParaView cannot take down the engine.
* Cost: data is extracted rather than shared; extraction is cached by
  `(run, time, preset-spec hash)`.
* Full volume rendering is available via "Open in ParaView" (we write a `.pvsm`-free
  Python state script that recreates the active scene).
