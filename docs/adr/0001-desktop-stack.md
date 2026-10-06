# ADR-0001 — Desktop stack: Electron/React shell, Python engine, ParaView sidecar

Status: Accepted (2026-10-06)

## Context

The product is a Windows-first automotive CFD workbench. It must provide excellent
Windows UX, GPU 3D, robust long-running process management, ParaView/VTK-grade
post-processing, automated testing, crash isolation and a path to Linux. The
simulation engine must be replaceable-UI-agnostic (MASTER §7).

Candidates considered:

| Option | Strengths | Weaknesses |
|---|---|---|
| **A. C++/Qt + ParaView ApplicationComponents** | Native embedded ParaView render views (full volume rendering, every filter, client/server); single toolkit | Must build ParaView from source with a matching MSVC toolchain and keep it in lock-step with each ParaView release (multi-hour builds, large CI burden); Qt licensing review for commercial distribution; slow UI iteration; UI automation harder; fewer engineers/agents fluent in it |
| **B. PySide6 + embedded VTK widget** | Fast prototyping, Python everywhere | Qt licensing (LGPL/commercial), packaging pains (PyInstaller + Qt + VTK + ParaView binaries), ParaView's pvpython ABI is not the app's Python, so ParaView cannot be imported in-process anyway |
| **C. Electron + React/TypeScript renderer, Python engine service, pvpython sidecar** | Best-in-class UI iteration and testing (Vitest/Playwright), WebGL2 3D (three.js), mature Windows packaging (electron-builder/NSIS), clean process isolation, engine fully headless & scriptable | WebGL viewport cannot do ParaView volume rendering natively; payloads must be extracted; larger install footprint |

## Decision

Option **C**.

* **Renderer:** React 19 + TypeScript + Vite, Mantine UI components, three.js viewport,
  ECharts plots. No Node integration in the renderer; a minimal `contextBridge` preload.
* **Main process:** Electron. Owns windows, native dialogs, backend lifecycle, opening
  external ParaView, printing reports to PDF.
* **Engine:** Python package `aerobench` (Pydantic domain model, case generation,
  providers, jobs, project DB, studies, optimisation, reports). Exposed through a
  FastAPI service bound to `127.0.0.1` with a random per-launch bearer token. The same
  engine is usable from the `aerobench` CLI and from tests without any GUI.
* **ParaView:** ParaView's own `pvpython` runs our sidecar script (ADR-0003). ParaView
  owns the scientific pipeline (readers, slices, stream tracers, gradients/Q,
  calculators); the app renders the *extracted* geometry + scalars in WebGL and uses
  ParaView's renderer for high-fidelity images. "Open in ParaView" remains available
  for anything not surfaced (e.g. volume rendering).

## Consequences

* The engine has no UI dependency; a Qt/ParaView-native front end could be added later
  without rewriting simulation logic (the API is the seam).
* In-app 3D is limited to surface-type outputs (surfaces, slices, iso-surfaces,
  streamlines, glyphs). This matches the automotive presets in BUILD_SPEC §17. Volume
  rendering is delegated to ParaView proper.
* We ship (or detect) three runtimes: Electron, a Python runtime for the engine, and
  ParaView's pvpython. Packaging (ADR-0010) must pin and record all three.
* Local services bind to localhost only and require the per-launch token (BUILD_SPEC §37).
