# Packaging and distribution

## Windows installer

```powershell
# on Windows 10/11 x64, from the repository root
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -PythonSha256 <sha256-of-embed-zip> [-NoCad]
```

Result: `desktop\release\AeroBench-Setup-<version>.exe` — an NSIS installer (per-user, no
administrator rights, choice of folder, Start-menu and desktop shortcuts).

What the script does:

1. Runs the engine unit tests in a throw-away virtual environment.
2. `packaging/prepare_python.py` downloads the official **embeddable CPython** (3.12, x64),
   verifies its SHA-256 (pass the digest published on python.org for release builds), enables
   `site-packages`, builds the engine wheel and installs it with all dependencies as
   **win_amd64 wheels only** (`--only-binary=:all:`), including OpenCASCADE for STEP/IGES/BREP
   import (the VTK-free `cadquery-ocp-novtk` build, ~48 MB download; `--no-cad` leaves it out). Windows-only transitive dependencies (e.g. `colorama`)
   are listed explicitly because pip evaluates markers for the build machine.
3. `packaging/licenses.py` writes `THIRD-PARTY-LICENSES.txt` with the licence of every shipped
   Python and JavaScript package; packages without a determinable licence are listed first
   so they cannot ship unnoticed.
4. Smoke-tests the embedded engine (`import`, `aerobench doctor`).
5. Builds the renderer (Vite) and the Electron main/preload (TypeScript).
6. `electron-builder` packages the app: renderer and main in `app.asar` (the renderer is also
   unpacked to `app.asar.unpacked/dist` because the engine serves it from disk), the Python
   runtime in `resources/python`, the licence file in `resources`. Source maps and
   `node_modules` are excluded — the renderer is a self-contained bundle.

The installer can also be cross-built on Linux with Wine (32- and 64-bit) installed:
`python3 packaging/prepare_python.py && cd desktop && npm run build && npx electron-builder --win nsis --x64`.

### Not bundled

OpenFOAM (GPL-3.0), Microsoft MPI and ParaView are **not** included. The Welcome screen and
`aerobench doctor` detect them and link to the official downloads. Do not add them to the
installer without a licence review (see [third-party.md](third-party.md)).

### Code signing

Unsigned installers trigger SmartScreen warnings. For release, sign with an EV or OV
code-signing certificate: set `CSC_LINK`/`CSC_KEY_PASSWORD` (or `WIN_CSC_LINK`) for
electron-builder, which signs `AeroBench.exe`, the uninstaller and the installer.

## Runtime layout (installed)

```
%LOCALAPPDATA%\Programs\AeroBench\
  AeroBench.exe                      Electron shell
  resources\app.asar                 main process + renderer
  resources\app.asar.unpacked\dist   renderer served by the engine
  resources\python\python.exe        embedded CPython with the aerobench engine
  resources\THIRD-PARTY-LICENSES.txt
%APPDATA%\AeroBench\settings.json    user settings (solver paths, cores, recent projects)
```

Projects live wherever the user creates them (default `Documents\AeroBench Projects`).

## Linux

`npm run dist:linux` produces an AppImage. On Linux the engine can also run from a normal
virtual environment (`AEROBENCH_PYTHON`), and OpenFOAM is taken from `openfoam2606`
packages (`/usr/lib/openfoam/openfoam2606/etc/bashrc`).

## Updating

Projects carry a schema version and are migrated on open (a backup snapshot is kept);
the project database applies numbered migrations. Runs keep the application version that
produced them, and numerics presets are versioned, so results stay reproducible across
updates.
