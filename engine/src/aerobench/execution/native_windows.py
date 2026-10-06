"""Native Windows provider: OpenCFD OpenFOAM (MinGW build) + Microsoft MPI.

Layout verified from ``OpenFOAM-v2606-windows-mingw.exe`` (see ADR-0002)::

    %APPDATA%\\Keysight-OpenCFD\\OpenFOAM\\v2606\\
        OpenFOAM-MSYS2.bat, setEnvVariables-v2606.bat
        msys64\\home\\ofuser\\OpenFOAM\\OpenFOAM-v2606\\          (WM_PROJECT_DIR)
            platforms\\win64MingwDPInt32Opt\\bin\\*.exe, *.dll     (all DLLs live in bin)
            etc\\controlDict ...

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
    """Return {'version','project_dir','bin_dir','thirdparty_lib'} for an install root."""
    ver = root.name if root.name.startswith("v") else None
    home = root / "msys64" / "home" / "ofuser" / "OpenFOAM"
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


def find_msmpi() -> MPIInfo:
    cands = []
    if os.environ.get("MSMPI_BIN"):
        cands.append(Path(os.environ["MSMPI_BIN"]) / "mpiexec.exe")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cands.append(Path(pf) / "Microsoft MPI" / "Bin" / "mpiexec.exe")
    launcher = next((str(c) for c in cands if c.exists()), None)
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    dll = Path(sysroot) / "System32" / "msmpi.dll"
    return MPIInfo(available=bool(launcher) and dll.exists(), kind="msmpi", launcher=launcher,
                   version=None if not dll.exists() else "installed")


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
        root = self.config.get("root")
        roots = [Path(root)] if root else candidate_roots(self.config.get("appdata"))
        pref = self.config.get("prefer_version")
        if pref:
            roots = sorted(roots, key=lambda r: r.name != pref)
        for r in roots:
            inst = locate_install(r)
            if inst["bin_dir"]:
                inst["root"] = str(r)
                return inst
        return None

    def detect(self) -> ProviderInfo:
        inst = self.install()
        problems: list[Diagnostic] = []
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
        if not mpi.available:
            problems.append(Diagnostic(
                code="environment.msmpi_missing", category=ErrorCategory.MPI, severity=Severity.ERROR,
                summary="Microsoft MPI is not installed.",
                likely_cause="The native OpenFOAM build links libPstream against msmpi.dll, so every OpenFOAM program needs it — even serial runs.",
                action="Install Microsoft MPI 10.x (msmpisetup.exe, bundled in the OpenFOAM installer's thirdParty folder or from Microsoft).",
                context={"download": "https://www.microsoft.com/en-us/download/details.aspx?id=57467"}))
        return ProviderInfo(
            id=self.id, kind=self.kind, label=f"OpenFOAM {inst['version']} for Windows (native)", available=mpi.available,
            fork="opencfd", version=inst["version"], project_dir=inst["project_dir"], bin_dir=inst["bin_dir"], mpi=mpi,
            dynamic_code=False, staging=self.staging, problems=problems, details={"root": inst["root"], "thirdparty_lib": inst["thirdparty_lib"]},
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
        return {
            "WM_PROJECT": "OpenFOAM",
            "WM_PROJECT_VERSION": info.version or "",
            "WM_PROJECT_DIR": pdir,
            "FOAM_SIGFPE": "1",
            "PATH": os.pathsep.join(p for p in path_parts if p),
        }

    def exec_path(self, host_path: Path) -> str:
        return win_path(host_path)

    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        info = self.info()
        exe = str(Path(info.bin_dir or "") / (app_argv[0] + ".exe"))
        argv = [exe] + list(app_argv[1:]) + ["-case", self.exec_path(case_dir)]
        if nprocs > 1:
            argv = [info.mpi.launcher or "mpiexec.exe", "-n", str(nprocs)] + argv + ["-parallel"]
        return CommandSpec(argv=argv, env=self.environment(), cwd=str(case_dir))
