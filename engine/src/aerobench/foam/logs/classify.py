"""Translate OpenFOAM/MPI/OS failures into engineering guidance (BUILD_SPEC §8.6, §31).

The classifier never hides the raw log: every Diagnostic carries the matched excerpt in
``details`` and the caller keeps the full log file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ...core.errors import Diagnostic, ErrorCategory, Severity

# Windows NTSTATUS codes as returned to Python (signed 32-bit)
STATUS_DLL_NOT_FOUND = -1073741515  # 0xC0000135
STATUS_ACCESS_VIOLATION = -1073741819  # 0xC0000005
STATUS_STACK_OVERFLOW = -1073741571
STATUS_CONTROL_C_EXIT = -1073741510


@dataclass
class Rule:
    code: str
    category: ErrorCategory
    pattern: re.Pattern
    summary: str
    cause: str
    action: str
    stages: tuple[str, ...] = ()


R = lambda p: re.compile(p, re.I | re.M)  # noqa: E731

RULES: list[Rule] = [
    Rule("environment.path_invalid", ErrorCategory.ENVIRONMENT, R(r"fileName::stripInvalid\(\) called for invalid fileName"),
         "OpenFOAM rejected the case path (it contains spaces or other characters invalid on Linux).",
         "OpenFOAM on Linux/WSL does not accept spaces in file names; the native Windows build does.",
         "Move the project to a folder without spaces, or update the application (it links such paths automatically)."),
    Rule("mesh.location_in_mesh", ErrorCategory.MESH,
         R(r"(locationInMesh|location in mesh|Point \(.*\) is not inside the mesh|not inside the mesh or on a face)"),
         "The meshing seed point (locationInMesh) is not inside the flow domain.",
         "The point lies inside the vehicle, outside the tunnel, or exactly on a cell face.",
         "Check the tunnel extents; the application normally places the seed near the inlet corner automatically.",
         ("mesh",)),
    Rule("mesh.memory", ErrorCategory.MEMORY, R(r"(std::bad_alloc|Out of memory|cannot allocate memory|not enough memory)"),
         "The process ran out of memory.",
         "The mesh is larger than the available RAM allows (refinement levels too fine or too many cells).",
         "Use a coarser mesh preset, fewer refinement levels, or run in parallel to spread memory across processes."),
    Rule("mesh.max_cells", ErrorCategory.MESH, R(r"(maxGlobalCells|Reached maximum number of cells|Stopping refining since)"),
         "Refinement stopped at the global cell limit.",
         "The requested refinement would exceed maxGlobalCells.",
         "Reduce refinement levels/volumes or raise the cell limit if memory allows.", ("mesh",)),
    Rule("geometry.surface_read", ErrorCategory.GEOMETRY, R(r"(Cannot read file.*triSurface|Could not read surface|Unknown file extension .* for triSurface)"),
         "snappyHexMesh could not read a geometry surface.",
         "A component surface file is missing or invalid.",
         "Regenerate the case; check the Geometry page for empty components."),
    Rule("solver.divergence", ErrorCategory.SOLVER, R(r"(Floating point exception|sigFpe|Floating-point exception|\bnan\b.*residual|Maximum number of iterations exceeded)"),
         "The solver diverged (floating-point exception / NaN).",
         "Numerical instability: poor mesh cells, too aggressive relaxation, inconsistent boundary conditions or a bad initial field.",
         "Re-run with the Robust numerics preset, check mesh quality (non-orthogonality, skewness) and layer collapse regions.",
         ("solve",)),
    Rule("config.missing_patch_bc", ErrorCategory.CONFIGURATION, R(r"Cannot find patchField entry for (\S+)"),
         "A boundary condition is missing for a mesh patch.",
         "The mesh contains a patch the field files do not define (e.g. geometry changed after case generation).",
         "Regenerate the solve case from the current mesh."),
    Rule("config.keyword", ErrorCategory.CONFIGURATION, R(r"(keyword (\S+) is undefined in dictionary|Entry '(\S+)' not found in dictionary)"),
         "A required dictionary entry is missing.",
         "An expert override removed a required keyword, or the dictionary syntax is not valid for this OpenFOAM version.",
         "Review Expert Mode overrides for the named file; the raw message names the keyword."),
    Rule("config.syntax", ErrorCategory.CONFIGURATION, R(r"(FOAM FATAL IO ERROR.*\n.*(on line|at line)|Expected a '\(' while reading|wrong token type)"),
         "OpenFOAM could not read a dictionary.",
         "Invalid syntax, typically introduced by an expert file override.",
         "Open the named file in Expert Mode; remove or fix the override."),
    Rule("mpi.failure", ErrorCategory.MPI, R(r"(MPI_ABORT|mpirun noticed|job aborted|smpd|mpiexec.*(failed|error)|ERROR: Error reported by MPI|PMPI_)"),
         "The parallel (MPI) run failed.",
         "MPI is not installed/configured, a rank crashed, or the decomposition does not match the process count.",
         "Check that MS-MPI (Windows) or Open MPI (Linux) is installed; retry with fewer cores; see the first rank error in the raw log."),
    Rule("mpi.decomposition_mismatch", ErrorCategory.MPI, R(r"(number of processor directories = \d+ is not equal to the number of processors = \d+|Cannot find processor directories)"),
         "The case decomposition does not match the number of processes.",
         "Stale processor* directories from a run with a different core count.",
         "The application re-decomposes automatically; if this persists, clean the run and restart."),
    Rule("disk.full", ErrorCategory.DISK, R(r"(No space left on device|There is not enough space on the disk|disk full)"),
         "The disk is full.", "Result/time directories or meshes used all free space.",
         "Free disk space, reduce the write interval or keep fewer time directories (purgeWrite)."),
    Rule("environment.dll_missing", ErrorCategory.ENVIRONMENT, R(r"(msmpi\.dll|The code execution cannot proceed|cannot open shared object file|error while loading shared libraries)"),
         "A required runtime library could not be loaded.",
         "Microsoft MPI is not installed (the native Windows OpenFOAM build needs msmpi.dll even for serial runs), or the OpenFOAM installation is incomplete.",
         "Install Microsoft MPI 10.x (msmpisetup.exe) and restart the application."),
    Rule("solver.wall_function_yplus", ErrorCategory.SOLVER, R(r"(yPlus.*too (low|high))"),
         "Near-wall resolution is inconsistent with the wall treatment.", "y+ outside the intended range.",
         "Adjust the boundary-layer target y+ and re-mesh."),
]


def classify(text: str, stage: str = "", exit_code: Optional[int] = None, extra_context: Optional[dict] = None) -> list[Diagnostic]:
    """Return diagnostics for a failed (or suspicious) step.  Most specific first."""
    out: list[Diagnostic] = []
    seen: set[str] = set()
    tail = text[-200_000:]
    for rule in RULES:
        if rule.stages and stage and stage not in rule.stages:
            continue
        m = rule.pattern.search(tail)
        if m and rule.code not in seen:
            seen.add(rule.code)
            start = max(0, tail.rfind("\n", 0, max(0, m.start() - 600)))
            excerpt = tail[start : m.end() + 800].strip()
            out.append(Diagnostic(code=rule.code, category=rule.category, severity=Severity.ERROR, summary=rule.summary,
                                  likely_cause=rule.cause, action=rule.action, details=excerpt, context=dict(extra_context or {})))
    if exit_code is not None:
        if exit_code == STATUS_DLL_NOT_FOUND and "environment.dll_missing" not in seen:
            out.append(Diagnostic(code="environment.dll_missing", category=ErrorCategory.ENVIRONMENT,
                                  summary="A required DLL was not found when starting OpenFOAM (STATUS_DLL_NOT_FOUND).",
                                  likely_cause="Microsoft MPI (msmpi.dll) is not installed; the native OpenFOAM v2606 build needs it even for serial runs.",
                                  action="Install Microsoft MPI 10.x and restart the application.", details=f"exit code {exit_code}"))
        elif exit_code in (137, -9) and "mesh.memory" not in seen:
            out.append(Diagnostic(code="mesh.memory", category=ErrorCategory.MEMORY, summary="The process was killed (likely out of memory).",
                                  likely_cause="The operating system terminated the process (SIGKILL), typically by the out-of-memory killer.",
                                  action="Reduce the mesh size or run in parallel.", details=f"exit code {exit_code}"))
        elif exit_code == STATUS_ACCESS_VIOLATION:
            out.append(Diagnostic(code="solver.crash", category=ErrorCategory.SOLVER, summary="OpenFOAM crashed (access violation).",
                                  likely_cause="Usually a consequence of divergence or invalid input data.",
                                  action="Inspect the raw log; try the Robust numerics preset.", details=f"exit code {exit_code}"))
    if not out and exit_code not in (None, 0):
        fatal = _fatal_excerpt(text)
        out.append(Diagnostic(code=f"{stage or 'step'}.failed", category=ErrorCategory.SOLVER if stage == "solve" else ErrorCategory.MESH if stage == "mesh" else ErrorCategory.INTERNAL,
                              summary=_fatal_headline(fatal) or f"The step exited with code {exit_code}.",
                              likely_cause="See the OpenFOAM message below.", action="Read the raw log excerpt; the first FATAL ERROR names the cause.",
                              details=fatal or text[-4000:]))
    return out


def _fatal_excerpt(text: str) -> str:
    i = text.rfind("FOAM FATAL")
    if i < 0:
        return ""
    j = text.find("FOAM exiting", i)
    j = text.find("FOAM aborting", i) if j < 0 else j
    return text[max(0, i - 200) : (j + 13 if j > 0 else i + 3000)].strip()


def _fatal_headline(excerpt: str) -> str:
    for ln in excerpt.splitlines():
        s = ln.strip()
        if s and not s.startswith("-->") and not s.startswith("From") and "FOAM FATAL" not in s and not s.startswith("("):
            return "OpenFOAM: " + s[:240]
    return ""
