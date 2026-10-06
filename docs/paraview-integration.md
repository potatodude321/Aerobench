# ParaView integration

ParaView is the post-processing **engine** of AeroBench, not a button: it reads the
OpenFOAM results, runs the filters (slices, clips, stream tracers, gradients, contours),
renders publication images and feeds extracted geometry to the in-app 3D viewer. The
decision and its alternatives are recorded in [ADR-0003](adr/0003-paraview-integration.md).

## Versions and detection

* **ParaView 6.2** is the validated version; any 6.x is accepted with a notice; 5.12–5.13 is
  best effort; older versions are refused.
* Detection order: path configured in Settings → `AEROBENCH_PVPYTHON` → standard install
  locations (`%ProgramFiles%\ParaView*\bin\pvpython.exe` and `%LOCALAPPDATA%\Programs\ParaView*`
  on Windows; `/opt/paraview/*`, `/opt/ParaView*`, `~/ParaView*`, `/usr/bin` on Linux; the
  application bundle on macOS). The version is read from the install folder name and
  confirmed by `pvpython` on first use.
* Without ParaView, everything except field visualisation and report images works: forces,
  coefficients, convergence, studies and optimisation never depend on ParaView.

## The sidecar

* The engine starts `pvpython --force-offscreen-rendering pv_server.py` (one process per
  open result session, pooled and restarted on failure). The server script imports only
  ParaView, VTK and NumPy.
* Protocol: one JSON request per line on stdin, one prefixed JSON response per line on
  stdout (`@@AB@@{…}`); everything else ParaView prints is ignored; errors return a message
  and traceback, never crash the engine.
* Readers: one `OpenFOAMReader` for boundary patches (surfaces) and a separate one for the
  internal mesh (volume filters) so surface requests never touch the volume data.
* Operations: `open_case` (patches, fields, times, ranges), `surface` (selected patches, one
  field), `slice` (plane, optional clip box), `streamlines` (seed line/plane, integration
  limits), `isosurface` (Q-criterion with the vehicle-normalised threshold `Q·(L/U)²`),
  `line_sample`, `probe`, `screenshot` (multiple layers, camera, size, colour map, range,
  scalar bar).
* Derived fields are computed in ParaView: `Cp = (p − p∞)/(½U∞²)` for kinematic pressure,
  `|U|/U∞`, vorticity and Q from `Gradient`.
* Colour-map names are resolved with aliases across ParaView releases (for example
  "Viridis (matplotlib)" in 5.x is "Viridis" in 6.x) with a safe fallback, so a renamed
  preset never fails a render.

## Data to the renderer

Extracts are written to the project cache as **ABG1** files: a JSON header (blocks, point
count, scalar range, part names, metadata) followed by little-endian float32 positions,
uint32 indices or polylines, float32 scalars/vectors and uint16 part ids. The three.js
viewer colours them with the same colour maps as ParaView and shows a matching legend.
Large surfaces are decimated by ParaView (`Decimate`) and the ratio is reported.
Extracts are cached by run, time and a hash of the request.

## Automotive presets

Generated from the run's geometry and reference values (`viz/presets.py`):

| Preset | Content |
|---|---|
| Cp body map | Pressure coefficient on all vehicle patches, range −1.5…1 |
| y+ body map | Near-wall resolution check (0…300) |
| Wall shear stress | Magnitude on vehicle patches |
| Centreline pressure / velocity | y = 0 plane, clipped around the vehicle |
| Rear wake plane | x-normal plane behind the vehicle |
| Front-/rear-wheel wake | x-normal planes behind each axle |
| Underfloor velocity | z-normal plane between floor and ground |
| Diffuser plane | Plane through the diffuser exit when present |
| Streamlines | Seeded upstream across the vehicle width and on the centreline |
| Q-criterion wake | Iso-surface at Q·(L/U)² = 50 coloured by |U|/U∞ |

## Expert access

* "Open in ParaView" launches the full ParaView GUI on the run's `case.foam`.
* "Export pvpython script" writes a stand-alone script that recreates the selected presets
  with standard ParaView filters, for users who continue in ParaView.
* Scene settings (camera, field, range, colour map, layers) are stored in the project
  database and can be re-applied to another run for comparison.

## Reports

Report images are rendered by ParaView (`SaveScreenshot`) at print resolution on a white
background with scaled scalar bars, embedded in the HTML as PNG data, so a report is a single
file that can be archived or printed without the project.
