# Packaging and distribution

## Windows installer

```powershell
# on Windows 10/11 x64, from the repository root (needs Python, Node.js, Git and 7-Zip)
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -PythonSha256 <sha256-of-embed-zip> [-NoCad]
```

Results:

* `desktop\release\AeroBench-Setup-<version>.exe` — one NSIS installer (≈1 GB) with
  everything the application needs: OpenFOAM v2606 for Windows, Microsoft MPI 10.1.3 and
  ParaView 6.2.0 (ADR-0010). Per-machine install to Program Files with one administrator
  prompt; choice of folder; Start-menu and desktop shortcuts.
* `packaging\build\sources\` — the GPL Corresponding Source for the bundled OpenFOAM
  (`OpenFOAM-v2606.tgz`, `ThirdParty-v2606.tar.gz`, `SOURCE-OFFER.txt`). **Publish these
  next to every installer you distribute** (see [third-party.md](third-party.md)).

What the script does:

1. Runs the engine unit tests in a throw-away virtual environment.
2. `packaging/prepare_thirdparty.py` downloads the OpenFOAM, MS-MPI and ParaView packages
   (each pinned by SHA-256; a changed upstream file stops the build), extracts the
   `OpenFOAM-v2606` tree from OpenCFD's installer (without its MSYS2 terminal and
   tutorials), trims ParaView (removes the NVIDIA IndeX plugin and `uv`; replaces 2.2 GB of
   identical duplicate DLLs by a hard-link list restored at install time) and writes
   `packaging/build/bundle` plus the source archives.
3. `packaging/prepare_python.py` downloads the official **embeddable CPython** (3.12, x64),
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
   runtime in `resources/python`, the bundled applications and the licence file in
   `resources`. Source maps and `node_modules` are excluded.

### What the setup does on the user's machine

1. Copies the application (≈3.5 GB installed; the installer unpacks to a temporary folder
   first, so about 7 GB must be free during installation).
2. Precompiles the Python modules of the engine and of ParaView (`compileall`). The
   installation folder is read-only for normal users, so without this every start would
   recompile them in memory and the application would start noticeably slower.
3. Recreates ParaView's duplicate DLLs as NTFS hard links (`resources\tools\restore_links.py`
   run with the embedded Python; copies where hard links are impossible). Idempotent, so a
   repair install fixes a damaged tree.
4. Installs Microsoft MPI 10.1.3 silently (`msmpisetup.exe -unattend`) unless an MS-MPI is
   already installed. Failure is reported; the app then offers *Install Microsoft MPI*.
5. The uninstaller removes the application folder; Microsoft MPI stays (shared component).

Silent installation for IT deployment: `AeroBench-Setup-<version>.exe /S [/D=C:\Path]`.

The installer can also be cross-built on Linux with Wine (32- and 64-bit) installed:
`python3 packaging/prepare_python.py && cd desktop && npm run build && npx electron-builder --win nsis --x64`.
To try the setup under Wine, give it a display and enough disk space:
`xvfb-run -a wine release/AeroBench-Setup-<version>.exe /S` (without a display it waits
forever; without about 7 GB free it stops with exit code 2).

### Bundled applications and updates

Versions and hashes live in `packaging/prepare_thirdparty.py` (`DOWNLOADS`). Updating OpenFOAM
or ParaView means changing a pin, rebuilding and re-running the validation benchmarks.
WSL is deliberately not bundled (ADR-0010).

### Code signing

Unsigned installers trigger SmartScreen warnings. For release, sign with an EV or OV
code-signing certificate: set `CSC_LINK`/`CSC_KEY_PASSWORD` (or `WIN_CSC_LINK`) for
electron-builder, which signs `AeroBench.exe`, the uninstaller and the installer. Bundled
third-party executables are excluded by a signing hook (`desktop/build-resources/sign.js`)
so they keep their vendors' own signatures (Microsoft for MS-MPI, the PSF for Python).

## Runtime layout (installed)

```
C:\Program Files\AeroBench\
  AeroBench.exe                        Electron shell
  resources\app.asar                   main process + renderer
  resources\app.asar.unpacked\dist     renderer served by the engine
  resources\python\python.exe          embedded CPython with the aerobench engine
  resources\openfoam\OpenFOAM-v2606\   OpenFOAM for Windows (bundled)
  resources\ParaView-6.2.0\            ParaView (bundled)
  resources\redist\msmpisetup.exe      Microsoft MPI redistributable
  resources\licenses\, THIRD-PARTY-LICENSES.txt, bundle.json
C:\Program Files\Microsoft MPI\        installed by the setup
%APPDATA%\AeroBench\settings.json      user settings (solver paths, cores, recent projects)
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
