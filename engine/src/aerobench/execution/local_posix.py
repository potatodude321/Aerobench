"""Local Linux/macOS provider: an OpenFOAM installation activated by its ``etc/bashrc``.

The OpenFOAM environment is captured once (``bash -c 'source bashrc; env -0'``) and then
applications are executed directly with that environment — no shell per command.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, Diagnostic, ErrorCategory, Severity
from .provider import CommandSpec, ExecutionProvider, MPIInfo, ProviderInfo

CANDIDATE_BASHRCS = [
    "/usr/lib/openfoam/openfoam*/etc/bashrc",  # OpenCFD .deb/.rpm
    "/opt/openfoam*/etc/bashrc",  # Foundation .deb
    "/opt/OpenFOAM/OpenFOAM-*/etc/bashrc",
    os.path.expanduser("~/OpenFOAM/OpenFOAM-*/etc/bashrc"),
]

ENV_KEYS = re.compile(r"^(PATH|LD_LIBRARY_PATH|DYLD_LIBRARY_PATH|WM_.*|FOAM_.*|MPI_.*|OPAL_.*|PV_.*|ParaView_.*|CGAL_.*|SCOTCH_.*|PYTHONPATH)$")


def find_bashrcs() -> list[str]:
    found: list[str] = []
    env_dir = os.environ.get("WM_PROJECT_DIR")
    if env_dir and os.path.exists(os.path.join(env_dir, "etc", "bashrc")):
        found.append(os.path.join(env_dir, "etc", "bashrc"))
    for pat in CANDIDATE_BASHRCS:
        for p in sorted(glob.glob(pat), reverse=True):
            if p not in found:
                found.append(p)
    return found


def capture_environment(bashrc: str, timeout: float = 60) -> dict[str, str]:
    script = f'source "{bashrc}" >/dev/null 2>&1; env -0'
    out = subprocess.run(["bash", "-c", script], capture_output=True, timeout=timeout, env={"HOME": os.environ.get("HOME", "/tmp"), "PATH": "/usr/bin:/bin"})
    env: dict[str, str] = {}
    for item in out.stdout.split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode()] = v.decode(errors="replace")
    return env


def _fork_version(project_dir: str, env: dict[str, str]) -> tuple[str, str]:
    ver = env.get("WM_PROJECT_VERSION", "")
    api = Path(project_dir) / "META-INFO" / "api-info"
    if api.exists() or ver.startswith("v"):
        return "opencfd", ver if ver.startswith("v") else f"v{ver}"
    return "foundation", ver


class LocalPosixProvider(ExecutionProvider):
    kind = "local-posix"
    staging = "in_place"

    def __init__(self, provider_id: str = "local", config: Optional[dict[str, Any]] = None):
        super().__init__(provider_id, config)
        self._env: Optional[dict[str, str]] = None

    def bashrc(self) -> Optional[str]:
        if self.config.get("bashrc"):
            return self.config["bashrc"]
        cands = find_bashrcs()
        pref = self.config.get("prefer_version")
        if pref:
            for c in cands:
                if pref in c:
                    return c
        return cands[0] if cands else None

    def foam_env(self) -> dict[str, str]:
        if self._env is None:
            rc = self.bashrc()
            if not rc:
                raise FileNotFoundError("no OpenFOAM bashrc found")
            full = capture_environment(rc)
            self._env = {k: v for k, v in full.items() if ENV_KEYS.match(k)}
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                # Open MPI refuses to run as root without explicit consent (containers / CI)
                self._env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
                self._env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"
        return self._env

    def detect(self) -> ProviderInfo:
        rc = self.bashrc()
        if not rc:
            return ProviderInfo(
                id=self.id, kind=self.kind, label="Local OpenFOAM (Linux/macOS)", available=False,
                problems=[Diagnostic(code="environment.openfoam_missing", category=ErrorCategory.ENVIRONMENT,
                                     summary="No OpenFOAM installation was found on this machine.",
                                     likely_cause="OpenFOAM is not installed or its etc/bashrc is in a non-standard location.",
                                     action="Install OpenCFD OpenFOAM v2606 (e.g. apt install openfoam2606) or set its bashrc path in Settings.")],
            )
        problems: list[Diagnostic] = []
        try:
            env = self.foam_env()
        except Exception as exc:  # noqa: BLE001
            return ProviderInfo(id=self.id, kind=self.kind, label="Local OpenFOAM", available=False,
                                problems=[Diagnostic(code="environment.openfoam_env", category=ErrorCategory.ENVIRONMENT,
                                                     summary="Could not activate the OpenFOAM environment.", details=str(exc))])
        pdir = env.get("WM_PROJECT_DIR", str(Path(rc).parent.parent))
        fork, ver = _fork_version(pdir, env)
        appbin = env.get("FOAM_APPBIN")
        solver_ok = bool(appbin and Path(appbin, "simpleFoam").exists())
        if not solver_ok:
            problems.append(Diagnostic(code="environment.solver_missing", category=ErrorCategory.ENVIRONMENT,
                                       summary="simpleFoam was not found in the OpenFOAM installation.", details=str(appbin)))
        import shutil

        mpirun = shutil.which("mpirun", path=env.get("PATH"))
        mpi = MPIInfo(available=bool(mpirun), kind="openmpi" if mpirun else None, launcher=mpirun)
        if mpirun:
            try:
                v = subprocess.run([mpirun, "--version"], capture_output=True, text=True, timeout=20, env={**os.environ, **env})
                mpi.version = (v.stdout or v.stderr).splitlines()[0] if (v.stdout or v.stderr) else None
            except Exception:  # noqa: BLE001
                pass
        else:
            problems.append(Diagnostic(code="environment.mpi_missing", category=ErrorCategory.MPI, severity=Severity.WARNING,
                                       summary="mpirun was not found: only serial runs are possible.",
                                       action="Install Open MPI (e.g. apt install openmpi-bin)."))
        return ProviderInfo(
            id=self.id, kind=self.kind, label=f"Local OpenFOAM {ver} ({fork})", available=solver_ok,
            fork=fork, version=ver, project_dir=pdir, bin_dir=appbin, mpi=mpi, dynamic_code=True,
            staging=self.staging, problems=problems, details={"bashrc": rc, "options": env.get("WM_OPTIONS")},
        )

    @staticmethod
    def _link_base() -> Path:
        """Private (0700, owned by this user) folder for space-free case links: a shared,
        predictable location would let another local user substitute the case."""
        import stat as _stat

        env = os.environ.get("AEROBENCH_LINK_DIR")
        if env:
            base = Path(env)
        else:
            rt = os.environ.get("XDG_RUNTIME_DIR")
            base = Path(rt) / "aerobench-links" if rt and os.path.isdir(rt) else Path.home() / ".cache" / "aerobench" / "links"
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = os.lstat(base)
        unsafe = _stat.S_ISLNK(st.st_mode) or (st.st_mode & 0o022) or (hasattr(os, "getuid") and st.st_uid != os.getuid())
        if unsafe:
            raise AeroBenchError.make("environment.link_dir_unsafe", ErrorCategory.ENVIRONMENT,
                                      f"The folder for case links ({base}) is not private to this user.",
                                      action="Remove it or set AEROBENCH_LINK_DIR to a folder only you can write to.")
        return base

    @staticmethod
    def safe_case_path(case_dir: Path) -> str:
        """OpenFOAM on POSIX rejects file names with spaces (``allowSpaceInFileName`` is read
        too late to help ``-case``).  Route such cases through a space-free symlink in a
        private per-user folder."""
        import hashlib
        import stat as _stat

        s = str(case_dir)
        if not any(c in s for c in ' \t"\''):
            return s
        link = LocalPosixProvider._link_base() / hashlib.sha256(s.encode()).hexdigest()[:24]
        try:
            st = os.lstat(link)
            if _stat.S_ISLNK(st.st_mode):
                if os.readlink(link) == s:
                    return str(link)
                link.unlink()
            else:
                raise AeroBenchError.make("environment.link_conflict", ErrorCategory.ENVIRONMENT,
                                          f"{link} exists and is not a link created by this application.")
        except FileNotFoundError:
            pass
        try:
            link.symlink_to(s, target_is_directory=True)
        except FileExistsError:
            if not (link.is_symlink() and os.readlink(link) == s):
                raise
        return str(link)

    def exec_path(self, host_path: Path) -> str:
        return self.safe_case_path(host_path)

    def check_case(self, case_dir: Path, nprocs: int) -> list[Diagnostic]:
        if nprocs > 1 and " " in str(case_dir):
            return [Diagnostic(
                code="environment.path_spaces_parallel", category=ErrorCategory.ENVIRONMENT,
                summary="Parallel OpenFOAM runs on Linux cannot use a project folder whose path contains spaces.",
                likely_cause="OpenFOAM resolves the physical case path in parallel and rejects spaces (the native Windows build allows them).",
                action="Move or save the project to a folder without spaces, or run on 1 core.",
                details=str(case_dir))]
        return []

    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        env = dict(self.foam_env())
        case = self.safe_case_path(case_dir)
        env["PWD"] = case
        argv = list(app_argv) + ["-case", case]
        if nprocs > 1:
            info = self.info()
            launcher = info.mpi.launcher or "mpirun"
            argv = [launcher, "-np", str(nprocs)] + argv + ["-parallel"]
        return CommandSpec(argv=argv, env=env, cwd=case)
