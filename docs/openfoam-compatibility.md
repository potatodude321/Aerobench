# OpenFOAM compatibility

## Supported targets

| Target | Status | Notes |
|---|---|---|
| **OpenCFD OpenFOAM v2606, native Windows** (MinGW build + MS-MPI) | Primary target, **bundled** | Ships in the AeroBench installer (ADR-0010) and is used by default; a separately installed copy can be selected. Verified layout and behaviour from the official installer (ADR-0002); serial pipeline and graceful stop tested with the real Windows binaries under Wine (`test_native_windows_wine.py`). Parallel (MS-MPI) needs a Windows test machine. |
| OpenCFD OpenFOAM v2606, Linux (`openfoam2606` packages) | Supported, continuously tested | Development and CI target; the integration suite runs the full pipeline against it. |
| OpenCFD OpenFOAM v2606 inside WSL2 | Removed (0.1.0 build 3) | The included native Windows build is the supported path; WSL may return once Windows is mature. |
| Other OpenCFD releases (v2312 … v2512) | Expected to work | Same dictionary dialect; the version is recorded with every run. Not part of the regression suite. |
| OpenFOAM Foundation v13/v14 | **Experimental adapter** | Disabled unless "allow experimental adapters" is set. Golden files document the output; not validated. |
| Remote Linux / HPC | Interface defined | Provider and staging model in place; transport (SSH/scheduler) to be completed (roadmap). |

## Native Windows facts (OpenCFD v2606)

Verified by unpacking `OpenFOAM-v2606-windows-mingw.exe`:

* Per-user install under `%APPDATA%\Keysight-OpenCFD\OpenFOAM\v2606\`
  (older releases used `ESI-OpenCFD`; both are searched).
* `WM_PROJECT_DIR` = `…\msys64\home\ofuser\OpenFOAM\OpenFOAM-v2606`.
* All executables **and** DLLs are in `platforms\win64MingwDPInt32Opt\bin`.
* `libPstream.dll` is the MS-MPI variant, so **Microsoft MPI must be installed even for
  serial runs**; a missing `msmpi.dll` is reported as a blocking environment problem with a
  download link (and the Windows `STATUS_DLL_NOT_FOUND` exit code is translated).
* The vendor `setEnvVariables-v2606.bat` has a malformed `WM_THIRD_PARTY_DIR`; AeroBench
  builds the environment itself (`WM_PROJECT_DIR`, `FOAM_ETC`, `MPI_BUFFER_SIZE`, `PATH`)
  and launches executables directly — no MSYS2 shell.
* Spaces in file names are accepted natively (`allowSpaceInFileName` is always on for
  Windows). Commands still run *in* the case directory instead of passing the case path,
  so no path ever goes through `mpiexec`'s command-line re-quoting.
* The MinGW build cannot compile code at run time. Generated cases therefore **never** use
  `#codeStream`, `coded*` boundary conditions or function objects, or `#calc`; a generator
  check rejects them (also for expert overrides).
* Parallel runs use `mpiexec -n N … -parallel` from MS-MPI; decomposition with scotch (default),
  hierarchical or simple.

## Linux notes

* The OpenFOAM environment is captured once from `etc/bashrc` and applications are executed
  directly with it (no shell per command).
* OpenFOAM on Linux rejects spaces in case paths. New projects get space-free folder names
  on Linux/macOS; serial runs in an existing path with spaces go through a space-free symbolic
  link; parallel runs in such a path are refused up front with an explanation (MPI ranks
  resolve the physical path).
* Open MPI refuses to run as root unless allowed; containers/CI set the documented
  `OMPI_ALLOW_RUN_AS_ROOT` variables automatically.

## What is generated (OpenCFD dialect)

| Item | Generated content |
|---|---|
| Background mesh | `blockMeshDict` sized from the vehicle extents and domain preset; cells cubic at the base size |
| Meshing | `snappyHexMeshDict` (surfaces per component with levels, feature edges from `surfaceFeatureExtract` data, refinement boxes/cylinders for wake, underbody, wheels, front, near-vehicle), layer controls from the layer design; `meshQualityDict`; `surfaceFeatureExtractDict` |
| Decomposition | `decomposeParDict` (scotch / hierarchical / simple) |
| Fields | `U`, `p`, `k`, `omega` / `epsilon` / `nuTilda`, `nut` with free-stream turbulence derived from intensity and viscosity ratio or length scale |
| Boundary conditions | Inlet `fixedValue` (zero yaw) or `freestreamVelocity`/`freestreamPressure` far field (yaw); outlet `inletOutlet` U with fixed p; sides/top `slip`; moving ground as `fixedValue` wall velocity (road mode: `U∞ cos β` along the vehicle axis; tunnel-belt mode: the free-stream vector) or `noSlip` when stationary; wheels `rotatingWallVelocity` about the wheel axis with ω = U_ground / R; wall functions per turbulence model; symmetry plane for half models |
| Numerics | Versioned presets: SIMPLE/SIMPLEC (`consistent`), GAMG for p, smoothSolver for transport equations, bounded Gauss upwind/linearUpwind(V)/limitedLinear schemes, limited gradients, relaxation per preset; optional first-order start followed by a switch to the final schemes |
| Function objects | `forces` (total and per component), `forceCoeffs`, `solverInfo` (residuals), `yPlus`, `wallShearStress`, `pressure` (Cp), `continuityError`, optional `vorticity`, `Q`, probes, and `abort` (`writeNow`) for clean stops |
| Initialisation | `potentialFoam -writephi` (optional) |

The force reduction to Cd, Cl, axle loads, balance and moments is done by AeroBench from
the raw `forces` output with the conventions of ADR-0007; the generated `forceCoeffs` is kept
as an independent cross-check (the integration suite asserts agreement).

## Foundation differences encapsulated by the adapter

`foamRun -solver incompressibleFluid` instead of `simpleFoam`; `constant/momentumTransport`
instead of `turbulenceProperties`; `constant/physicalProperties` instead of
`transportProperties`; `residuals` instead of `solverInfo`; `forceCoeffs` keyword spellings;
`potentialFoam -writePhi`. These are generated and covered by golden-file tests but have not
been run against a Foundation installation; the adapter stays experimental until they are.

## Dictionary handling

Dictionaries are built as an AST (`foam/ast.py`) and written deterministically; the parser
reads OpenFOAM syntax (macros, `#include`, `#eval`, regex keys, lists, uniform/nonuniform
fields, code blocks as opaque text). Round-tripping the OpenFOAM v2606 tutorial tree
(7,937 files) parses and rewrites ~99 % of dictionaries identically; the remainder are not
dictionaries (scripts, data tables). Expert overrides are applied to the AST and recorded
as a diff in the run provenance.

## Failure translation

Solver, mesher, MPI and operating-system failures are classified into engineering messages
(cause and action) while the raw log is always kept: location-in-mesh errors, memory
exhaustion, cell-count limits, missing surfaces, divergence (floating-point exceptions,
NaNs), missing patch fields, dictionary syntax, MPI aborts and decomposition mismatches,
full disks, missing DLLs/shared libraries, invalid file names, Windows NTSTATUS codes.
