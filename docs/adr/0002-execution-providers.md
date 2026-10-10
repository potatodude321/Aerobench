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
  the space-in-path restriction, and is the same model remote/HPC needs. Contract:
  * the host copy is authoritative: `stage_in` replaces the staged directory with a clean
    copy (no stale time steps or stop triggers survive from an earlier attempt) — but refuses
    when the staged copy holds a *later* time step than the host (results that were never
    copied back), so they cannot be destroyed; the staged directory is
    `<work_root>/<parent>/<case>-<hash of host path>` — shell-safe and never shared by two
    host cases;
  * while the copy is live, host-side edits between solver steps (scheme switch, endTime,
    clean-up) are mirrored with `push_files`/`remove_paths`, and a graceful stop writes the
    trigger into the copy (`write_file`; if that fails the solver is terminated instead);
  * applications (e.g. `decomposePar` on resume) only run after `stage_in`;
  * after a stop, a failure or a runner error the copy is copied back (*salvage*) so the
    latest written time and partial results are kept; the job stays `stopping` (not a final
    state) until the copy-back has finished, so nothing ingests or resumes a half-copied case;
    a failed stage-in is never copied back; a failed salvage reports the exact command that
    copies the results back. If a machine dies mid-run, progress since the last copy-back
    stays in the staged copy and the next stage-in refuses to overwrite it;
  * a staged provider that cannot mirror an edit fails the step rather than let the solver
    run on stale input;
  * WSL commands are `wsl.exe [-d distro] --exec bash -c <script>`: with plain `wsl.exe --`
    WSL hands the raw command line to the distribution's default shell, which would expand
    `$…` and backticks (also in host paths) before bash runs the script.
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

## Update (0.2.0)

The WSL provider was removed. The included native Windows build (OpenFOAM v2606 + MS-MPI)
is the only supported engine on Windows until it is fully mature; settings files that still
name a WSL engine are ignored. The staged-execution machinery remains for remote machines.
