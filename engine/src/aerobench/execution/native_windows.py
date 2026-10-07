"""Native Windows provider: OpenCFD OpenFOAM (MinGW build) + Microsoft MPI.

Two layouts are recognised (verified from ``OpenFOAM-v2606-windows-mingw.exe``, ADR-0002):

* **bundled** with AeroBench (default; ``<install>\\resources\\openfoam``)::

      openfoam\\OpenFOAM-v2606\\                                 (WM_PROJECT_DIR)
          platforms\\win64MingwDPInt32Opt\\bin\\*.exe, *.dll     (all DLLs live in bin)
          etc\\controlDict ...

* the **vendor installer** (a user's own installation)::

      %APPDATA%\\Keysight-OpenCFD\\OpenFOAM\\v2606\\
          msys64\\home\\ofuser\\OpenFOAM\\OpenFOAM-v2606\\...      (same tree)

Order of preference: a root configured in Settings, the bundled copy, vendor installs.

``bin\\libPstream.dll`` is the MS-MPI variant, so ``msmpi.dll`` must be resolvable even
for serial runs.  The environment is built here (the vendor .bat has a malformed
WM_THIRD_PARTY_DIR) and executables are launched directly — no MSYS2 shell.
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

from ..core.errors import Diagnostic, ErrorCategory, Severity
from .provider import CommandSpec, ExecutionProvider, MPIInfo, ProviderInfo

VENDOR_DIRS = ("Keysight-OpenCFD", "ESI-OpenCFD")


def candidate_roots(appdata: Optional[str] = None) -> list[Path]:
    appdata = appdata or os.environ.get("APPDATA", "")
    roots: list[Path] = []
    for vendor in VENDOR_DIRS:
        base = Path(appdata) / vendor / "OpenFOAM"
        if base.is_dir():
            for d in sorted(base.glob("v*"), reverse=True):
                if (d / "msys64").is_dir():
                    roots.append(d)
    return roots


def locate_install(root: Path) -> dict[str, Optional[str]]:
    """Return {'version','project_dir','bin_dir','thirdparty_lib'} for an install root (vendor
    ``...\\v2606`` with an msys64 tree, or a folder that directly contains ``OpenFOAM-v*``)."""
    ver = root.name if root.name.startswith("v") else None
    home = root / "msys64" / "home" / "ofuser" / "OpenFOAM"
    if not home.is_dir() and any(root.glob("OpenFOAM-v*")):
        home = root
    pdirs = sorted(home.glob("OpenFOAM-v*"), reverse=True) if home.is_dir() else []
    pdir = None
    if ver and (home / f"OpenFOAM-{ver}").is_dir():
        pdir = home / f"OpenFOAM-{ver}"
    elif pdirs:
        pdir = pdirs[0]
        ver = ver or pdir.name.split("-", 1)[1]
    bin_dir = None
    tp_lib = None
    if pdir is not None:
        for b in sorted(pdir.glob("platforms/*/bin")):
            if (b / "simpleFoam.exe").exists():
                bin_dir = b
                break
        tp = sorted(pdir.glob("ThirdParty*/platforms/*/lib")) + sorted(home.glob("ThirdParty*/platforms/*/lib"))
        tp_lib = tp[0] if tp else None
    return {
        "version": ver,
        "project_dir": str(pdir) if pdir else None,
        "bin_dir": str(bin_dir) if bin_dir else None,
        "thirdparty_lib": str(tp_lib) if tp_lib else None,
    }


MSMPI_MIN_MAJOR = 10  # the OpenFOAM v2606 Windows build is linked against MS-MPI 10.x


def msmpi_version(launcher: str, timeout: float = 15) -> Optional[str]:
    """``mpiexec`` prints "Microsoft MPI Startup Program [Version 10.1.12498.52]" in its usage."""
    import re
    import subprocess

    try:
        out = subprocess.run([launcher], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Version\s+(\d+\.\d+\.\d+(?:\.\d+)?)", out.stdout + out.stderr)
    return m.group(1) if m else None


def find_msmpi() -> MPIInfo:
    cands = []
    if os.environ.get("MSMPI_BIN"):
        cands.append(Path(os.environ["MSMPI_BIN"]) / "mpiexec.exe")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cands.append(Path(pf) / "Microsoft MPI" / "Bin" / "mpiexec.exe")
    launcher = next((str(c) for c in cands if c.exists()), None)
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    dll = Path(sysroot) / "System32" / "msmpi.dll"
    available = bool(launcher) and dll.exists()
    version = msmpi_version(launcher) if available and launcher else None
    return MPIInfo(available=available, kind="msmpi", launcher=launcher, version=version or ("installed" if available else None))


def win_path(p: Path | str) -> str:
    """Absolute Windows path with forward slashes (accepted by Win32 and OpenFOAM's fileName)."""
    s = str(PureWindowsPath(str(p)))
    return s.replace("\\", "/")


class NativeWindowsProvider(ExecutionProvider):
    kind = "native-windows"
    staging = "in_place"

    def __init__(self, provider_id: str = "native", config: Optional[dict[str, Any]] = None):
        super().__init__(provider_id, config)

    def install(self) -> Optional[dict[str, Optional[str]]]:
        from .. import bundle

        root = self.config.get("root")
        bundled = bundle.openfoam_root()
        if root:
            roots = [Path(root)]
        else:
            roots = ([bundled] if bundled else []) + candidate_roots(self.config.get("appdata"))
        pref = self.config.get("prefer_version")
        if pref:
            roots = sorted(roots, key=lambda r: r.name != pref)
        for r in roots:
            inst = locate_install(r)
            if inst["bin_dir"]:
                inst["root"] = str(r)
                inst["bundled"] = bool(bundled and Path(r) == bundled)
                return inst
        return None

    def detect(self) -> ProviderInfo:
        from .. import bundle

        inst = self.install()
        problems: list[Diagnostic] = []
        if not inst and bundle.bundle_dir() is not None:
            problems.append(Diagnostic(
                code="environment.openfoam_missing", category=ErrorCategory.ENVIRONMENT,
                summary="The OpenFOAM included with AeroBench is missing or damaged.",
                likely_cause=f"No OpenFOAM executables were found under {bundle.bundle_dir() / 'openfoam'} "
                             "(e.g. removed by antivirus software or an incomplete installation).",
                action="Run the AeroBench setup again to repair the installation."))
            return ProviderInfo(id=self.id, kind=self.kind, label="OpenFOAM for Windows (included)", available=False, problems=problems)
        if not inst:
            problems.append(Diagnostic(
                code="environment.openfoam_missing", category=ErrorCategory.ENVIRONMENT,
                summary="OpenFOAM for Windows (OpenCFD v2606, MinGW build) is not installed.",
                likely_cause="The official installer places OpenFOAM under %APPDATA%\\Keysight-OpenCFD\\OpenFOAM\\v2606.",
                action="Download OpenFOAM-v2606-windows-mingw.exe from openfoam.com (dl.openfoam.com/source/v2606/) and run it, "
                       "or point Settings → Solver to your installation.",
                context={"download": "https://dl.openfoam.com/source/v2606/OpenFOAM-v2606-windows-mingw.exe"}))
            return ProviderInfo(id=self.id, kind=self.kind, label="OpenFOAM for Windows (native)", available=False, problems=problems)
        mpi = find_msmpi()
        installer = bundle.msmpi_installer()
        if not mpi.available:
            problems.append(Diagnostic(
                code="environment.msmpi_missing", category=ErrorCategory.MPI, severity=Severity.ERROR,
                summary="Microsoft MPI is not installed.",
                likely_cause="The native OpenFOAM build links libPstream against msmpi.dll, so every OpenFOAM program needs it — even serial runs. "
                             "The AeroBench setup installs it; it may have been skipped or removed.",
                action=("Click 'Install Microsoft MPI' (included with AeroBench; Windows asks for administrator approval)."
                        if installer else "Install Microsoft MPI 10.1.3 (msmpisetup.exe) from Microsoft."),
                context={"bundled_installer": str(installer) if installer else None,
                         "download": "https://www.microsoft.com/en-us/download/details.aspx?id=105289"}))
        else:
            major = None
            try:
                major = int(str(mpi.version).split(".")[0])
            except ValueError:
                pass
            if major is not None and major < MSMPI_MIN_MAJOR:
                problems.append(Diagnostic(
                    code="environment.msmpi_old", category=ErrorCategory.MPI, severity=Severity.WARNING,
                    summary=f"Microsoft MPI {mpi.version} is older than the 10.x this OpenFOAM build expects.",
                    action="Click 'Install Microsoft MPI' to update to the included 10.1.3." if installer else "Update Microsoft MPI to 10.1.3.",
                    context={"bundled_installer": str(installer) if installer else None}))
        label = f"OpenFOAM {inst['version']} for Windows" + (" (included)" if inst.get("bundled") else " (native)")
        return ProviderInfo(
            id=self.id, kind=self.kind, label=label, available=mpi.available,
            fork="opencfd", version=inst["version"], project_dir=inst["project_dir"], bin_dir=inst["bin_dir"], mpi=mpi,
            dynamic_code=False, staging=self.staging, problems=problems,
            details={"root": inst["root"], "thirdparty_lib": inst["thirdparty_lib"], "bundled": bool(inst.get("bundled")),
                     "msmpi_installer": str(installer) if installer else None},
        )

    def environment(self) -> dict[str, str]:
        info = self.info()
        pdir, bdir = info.project_dir or "", info.bin_dir or ""
        tp = (info.details or {}).get("thirdparty_lib")
        path_parts = [bdir]
        if tp:
            path_parts.append(tp)
        if info.mpi.launcher:
            path_parts.append(str(Path(info.mpi.launcher).parent))
        path_parts.append(os.environ.get("PATH", ""))
        env = {
            "WM_PROJECT": "OpenFOAM",
            "WM_PROJECT_VERSION": info.version or "",
            "WM_PROJECT_DIR": pdir,
            "FOAM_ETC": str(Path(pdir) / "etc") if pdir else "",
            "FOAM_SIGFPE": "1",
            # buffered sends in Pstream need an attached MPI buffer (same default as etc/bashrc)
            "MPI_BUFFER_SIZE": os.environ.get("MPI_BUFFER_SIZE", "20000000"),
            "PATH": os.pathsep.join(p for p in path_parts if p),
        }
        if "USER" not in os.environ and os.environ.get("USERNAME"):
            env["USER"] = os.environ["USERNAME"]
        return env

    def exec_path(self, host_path: Path) -> str:
        return win_path(host_path)

    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        """The case is the working directory (OpenFOAM's default case is ``.``): no case path
        travels through mpiexec's command-line re-quoting, so folders with spaces or non-ASCII
        characters are safe.  MS-MPI starts local ranks in mpiexec's working directory."""
        info = self.info()
        exe = str(Path(info.bin_dir or "") / (app_argv[0] + ".exe"))
        argv = [exe] + list(app_argv[1:])
        if nprocs > 1:
            argv = [info.mpi.launcher or "mpiexec.exe", "-n", str(nprocs)] + argv + ["-parallel"]
        return CommandSpec(argv=argv, env=self.environment(), cwd=str(case_dir))
