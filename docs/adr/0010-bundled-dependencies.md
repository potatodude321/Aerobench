# ADR-0010 — One installer with OpenFOAM, MS-MPI and ParaView

Status: accepted (0.1.0)

## Context

The product's appeal is that an engineer who has never installed OpenFOAM gets a working
external-aero workbench. Installing OpenFOAM for Windows, Microsoft MPI and ParaView by hand
(three vendors, an elevation prompt, paths to configure) is exactly the friction to remove.

Benchmark: SimFlow's Windows installer bundles OpenFOAM, ParaView and MS-MPI and installs to
Program Files; at the end it launches the MS-MPI wizard for the user to click through.

## Decision

1. **One per-machine NSIS installer** (one administrator prompt) containing:
   * OpenFOAM v2606 for Windows — the `OpenFOAM-v2606` tree extracted from OpenCFD's official
     installer (executables, libraries, `etc`), without the MSYS2 terminal environment and
     tutorials, which AeroBench does not use. AeroBench builds the environment itself and
     launches executables directly (ADR-0002), so no vendor scripts are needed.
   * Microsoft MPI 10.1.3 — the official redistributable, installed **silently**
     (`msmpisetup.exe -unattend`) when no MS-MPI is present. An existing MS-MPI is kept;
     a version older than 10 is reported with a one-click update in the app. The Environment
     card can also run the bundled installer later (repair path).
   * ParaView 6.2.0 — Kitware's Windows binaries. The ZIP stores ~1000 DLLs up to four
     times (2.2 GB of identical copies); the copies are dropped from the package and recreated
     as NTFS hard links after installation, so the installed tree is Kitware's layout at no
     extra disk cost. The NVIDIA IndeX plugin (290 MB, needs its own licence and an NVIDIA GPU)
     and the `uv` tool are removed.
2. Every download is **pinned by SHA-256** (`packaging/prepare_thirdparty.py`); a changed
   upstream file stops the build.
3. Detection prefers the **bundled** copies; a user's own OpenFOAM/ParaView can still be
   selected in Settings.
4. **WSL is not bundled.** It would need Windows optional features and a reboot, CPU
   virtualisation enabled in the firmware, and a Linux distribution with OpenFOAM inside it
   (~1 GB more) — more steps and failure modes for a newcomer, for no benefit over the native
   build. SimFlow does not use WSL either. WSL stays an optional advanced engine.
5. **GPL compliance** for OpenFOAM: GPL text and a source offer in the installation; the
   Corresponding Source archives are produced by the build and published next to every
   installer (see third-party.md).

## Consequences

* Installer ≈ 1 GB, installed ≈ 3.5 GB (ParaView dominates).
* Upgrading OpenFOAM or ParaView is a new AeroBench release (pins updated, re-validated).
* Verification without Windows hardware: the bundled Windows OpenFOAM is exercised by the
  real `NativeWindowsProvider` under Wine (serial pipeline, graceful stop; Wine cannot run
  MS-MPI's process manager, so parallel runs need a Windows machine), and the bundled ParaView
  runs the ParaView integration tests under Wine (`tests/integration/test_native_windows_wine.py`,
  `test_viz_paraview.py` with a Wine `pvpython` shim).
