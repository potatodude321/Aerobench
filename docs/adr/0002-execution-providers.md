# ADR-0002 — Execution providers

Status: Accepted (2026-10-06)

## Context

OpenFOAM must run natively on Windows (OpenCFD v2606 MinGW build + MS-MPI), inside WSL2,
on Linux workstations/CI, and later on remote Linux/HPC. These differ in path syntax,
environment set-up, MPI launcher, filesystem semantics (case sensitivity, 9P performance),
and capabilities (the MinGW build cannot compile dynamic code).

Facts verified by unpacking `OpenFOAM-v2606-windows-mingw.exe` (2026-10-06):

* Installs to `%APPDATA%\Keysight-OpenCFD\OpenFOAM\v2606\` (NSIS installer, per-user).
* Project dir: `<root>\msys64\home\ofuser\OpenFOAM\OpenFOAM-v2606` (`WM_PROJECT_DIR`).
* All executables **and DLLs** live in `platforms\win64MingwDPInt32Opt\bin` (no `lib/`).
* `bin\libPstream.dll` is byte-identical in size to `libPstream.dll-msmpi`; a
  `libPstream.dll-dummy` is shipped alongside. Because Windows resolves DLLs from the
  executable's directory first, **MS-MPI (`msmpi.dll`) must be installed even for serial
  runs.**
* The shipped `setEnvVariables-v2606.bat` has a malformed `WM_THIRD_PARTY_DIR`
  (`%WM_PROJECT_DIR\%ThirdParty-...`). We therefore construct the environment ourselves.
* `etc/controlDict`: `allowSpaceInFileName` is "Ignored (always 1) for Windows", so
  paths such as `E:\OpenFOAM GUI\...` are valid natively. On Linux/WSL spaces are
  rejected by default.
* README: "The present MSYS2 terminal does not support the compilation of OpenFOAM or
  dynamic code compilation".

## Decision

A provider interface (`aerobench.execution.provider.ExecutionProvider`) with:

* `detect()` → `ProviderInfo` (fork, version, paths, MPI availability, capabilities,
  problems with remediation).
* `build_command(app, args, case, nprocs)` → `CommandSpec` (argv, env, cwd) — never a
  shell string.
* `map_path()` between host paths and the execution side.
* `staging` mode: `in_place` (Native Windows, Local POSIX) or `staged` (WSL, Remote).
  Staged providers copy the case into a provider-local working directory (WSL ext4 or
  remote scratch), run there, and synchronise results back. This avoids 9P slowness and
  the space-in-path restriction, and is the same model remote/HPC needs.
* Capability flags: `dynamic_code`, `parallel`, `mpi_launcher`.

Implementations: `native-windows` (default on Windows), `local-posix` (Linux/macOS,
CI and development), `wsl` (advanced/fallback), `remote-ssh` (interface + configuration
now; transport implementation matures later).

Generated cases must **never** require dynamic code (`#codeStream`, `coded*`, `#calc`).
A generator test enforces this.

Every run records the provider id, fork, version, executable paths and environment
digest in its provenance.

## Consequences

* The app owns environment construction on Windows (no reliance on vendor `.bat`).
* Missing MS-MPI is surfaced as a blocking environment error with an install action.
* WSL/remote runs cost a sync step but gain correctness and speed.
