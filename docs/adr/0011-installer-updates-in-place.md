# ADR-0011 — The setup updates an installation in place

Status: accepted (0.2.0)

## Context

The 0.1.0 setup was a plain electron-builder NSIS installer. Run over an existing
installation it first ran the old uninstaller (deleting about 3.5 GB) and then unpacked
everything again: OpenFOAM, ParaView and the Python runtime, which rarely change between
releases, were removed and reinstalled every time. An update took as long as a first
installation, needed the same 7 GB of free space and failed in the same places (antivirus
scanning thousands of DLLs, long paths).

## Decision

1. **Components.** The large, rarely changing parts — the Python runtime, OpenFOAM,
   ParaView and CalculiX — are packed as separate 7-Zip archives
   (`packaging/make_components.py`). Each has a **content id**: a hash over the format
   version, its name and folder, and every file's path, size and SHA-256. Python bytecode
   caches and the marker file are excluded, so compiling modules after installation does not
   change the content.
2. **One setup for install and update.** On an existing installation the old uninstaller is
   not run (its registry entries are taken over); the old application files are removed and
   the components are decided one by one:
   * marker file `.aerobench-component` with this version's id and every file at its
     recorded size → **kept**;
   * no marker (installed by 0.1.0, before components existed) → kept only if every file is
     byte-identical to this version (SHA-256), extra files included; otherwise reinstalled;
   * anything else (different id, missing or damaged files) → removed and installed.
   The marker is written only after a component's post-installation steps (bytecode
   compilation, ParaView's hard links) have finished, so an interrupted setup repairs
   itself on the next run.
3. The check runs with the embedded Python (`resources\tools\components.py`, manifests in
   `resources\tools\components\`), installed first. Every decision goes to
   `resources\setup.log`.
4. Steps that are cheap and idempotent always run (engine bytecode, ParaView hard links,
   MS-MPI presence), so running the same setup again is a repair.
5. electron-builder's copy of the setup for its auto-updater (about 1 GB under
   `%LOCALAPPDATA%`), which AeroBench does not use, is deleted; the size shown in *Apps*
   is the real installed size.
6. The engine is installed as a folder (`resources\engine`) next to the Python runtime and
   found through the runtime's `._pth` file, so a new engine never changes the Python
   component; Python packages are pinned (`packaging/windows-constraints.txt`) for the same
   reason.

## Consequences

* An update that changes only the application rewrites a few hundred MB instead of 3.5 GB and
  keeps working when the user is short of disk space.
* The setup grows by nothing: unchanged archives are still embedded, so a first install from
  the same file works.
* A user who edits files inside a component folder gets that component reinstalled by the
  next setup (its marker no longer matches), which is the safe outcome.
* Verified under Wine: update from 0.1.0 (components adopted after the full comparison), a
  repeated setup (all kept by marker), a fresh install and uninstall.
