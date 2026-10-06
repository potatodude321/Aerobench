"""WSL2 provider (advanced/fallback): OpenFOAM inside a WSL distribution.

Cases are *staged* into the distribution's ext4 filesystem (fast, case-sensitive, a
shell-safe directory name per host case) and synchronised back at the end of the job, and
also after a stop or failure so partial results and the latest written time are kept.
Host-side edits between solver steps and the graceful-stop trigger are mirrored into the
staged copy (``push_files``/``remove_paths``/``write_file``).  All commands go
through ``wsl.exe -d <distro> --`` with an explicit ``bash -c`` that sources the
OpenFOAM bashrc; arguments are shell-quoted.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

from ..core.errors import Diagnostic, ErrorCategory
from .provider import CommandSpec, ExecutionProvider, MPIInfo, ProviderInfo, staged_case_key


def windows_to_wsl(p: Path | str) -> str:
    """``E:\\OpenFOAM GUI\\x`` -> ``/mnt/e/OpenFOAM GUI/x``."""
    w = PureWindowsPath(str(p))
    if not w.drive or len(w.drive) != 2:
        raise ValueError(f"not a drive path: {p}")
    rest = "/".join(w.parts[1:])
    return f"/mnt/{w.drive[0].lower()}/{rest}"


def list_distros(timeout: float = 15) -> list[str]:
    try:
        out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    text = out.stdout.decode("utf-16-le", errors="ignore") if b"\x00" in out.stdout else out.stdout.decode(errors="ignore")
    return [ln.strip().strip("\x00") for ln in text.splitlines() if ln.strip().strip("\x00")]


class WSLProvider(ExecutionProvider):
    kind = "wsl"
    staging = "staged"

    def __init__(self, provider_id: str = "wsl", config: Optional[dict[str, Any]] = None):
        super().__init__(provider_id, config)

    @property
    def distro(self) -> Optional[str]:
        return self.config.get("distro")

    @property
    def bashrc(self) -> str:
        return self.config.get("bashrc", "/usr/lib/openfoam/openfoam2606/etc/bashrc")

    @property
    def work_root(self) -> str:
        return self.config.get("work_root", "$HOME/.aerobench/work")

    def _wsl(self, script: str) -> list[str]:
        argv = ["wsl.exe"]
        if self.distro:
            argv += ["-d", self.distro]
        return argv + ["--", "bash", "-c", script]

    def detect(self) -> ProviderInfo:
        distros = list_distros()
        if not distros:
            return ProviderInfo(id=self.id, kind=self.kind, label="WSL2", available=False, staging=self.staging,
                                problems=[Diagnostic(code="environment.wsl_missing", category=ErrorCategory.ENVIRONMENT,
                                                     summary="No WSL distribution is installed.",
                                                     action="Install WSL2 and Ubuntu (wsl --install), then OpenFOAM inside it.")])
        distro = self.distro or distros[0]
        probe = f'source {shlex.quote(self.bashrc)} >/dev/null 2>&1 && echo "$WM_PROJECT_VERSION|$WM_PROJECT_DIR|$(command -v mpirun)"'
        try:
            out = subprocess.run(["wsl.exe", "-d", distro, "--", "bash", "-c", probe], capture_output=True, text=True, timeout=60)
            ver, pdir, mpirun = (out.stdout.strip().split("|") + ["", "", ""])[:3]
        except Exception:  # noqa: BLE001
            ver, pdir, mpirun = "", "", ""
        if not ver:
            return ProviderInfo(id=self.id, kind=self.kind, label=f"WSL2 ({distro})", available=False, staging=self.staging,
                                details={"distros": distros},
                                problems=[Diagnostic(code="environment.openfoam_missing", category=ErrorCategory.ENVIRONMENT,
                                                     summary=f"OpenFOAM was not found in WSL distribution '{distro}'.",
                                                     action="Inside WSL: curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash; "
                                                            "sudo apt install openfoam2606-default")])
        fork = "opencfd" if ver.startswith("v") else "foundation"
        return ProviderInfo(id=self.id, kind=self.kind, label=f"OpenFOAM {ver} in WSL ({distro})", available=True, fork=fork,
                            version=ver, project_dir=pdir, mpi=MPIInfo(available=bool(mpirun), kind="openmpi", launcher=mpirun or None),
                            dynamic_code=True, staging=self.staging, details={"distro": distro, "distros": distros})

    def remote_case_dir(self, case_dir: Path) -> str:
        return f"{self.work_root}/{staged_case_key(case_dir)}"

    def _rdir(self, case_dir: Path) -> str:
        """Shell expression for the remote case directory ($HOME / ~ expanded, rest quoted)."""
        rel = staged_case_key(case_dir)
        root = self.work_root.rstrip("/")
        for prefix in ("$HOME/", "~/"):
            if root.startswith(prefix):
                return '"$HOME"/' + shlex.quote(f"{root[len(prefix):]}/{rel}")
        if root in ("$HOME", "~"):
            return '"$HOME"/' + shlex.quote(rel)
        return shlex.quote(f"{root}/{rel}")

    @staticmethod
    def _rel(relpath: str) -> str:
        if relpath.startswith(("/", "~")) or ".." in Path(relpath).parts:
            raise ValueError(f"not a case-relative path: {relpath}")
        return relpath

    def exec_path(self, host_path: Path) -> str:
        return self.remote_case_dir(host_path)

    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        rdir = self._rdir(case_dir)
        app = " ".join(shlex.quote(a) for a in app_argv)
        if nprocs > 1:
            app = f"mpirun -np {int(nprocs)} {app} -parallel"
        script = f"source {shlex.quote(self.bashrc)} >/dev/null 2>&1 && cd {rdir} && exec {app} -case \"$PWD\""
        return CommandSpec(argv=self._wsl(script), cwd=str(case_dir))

    def stage_in(self, case_dir: Path) -> CommandSpec:
        src = shlex.quote(windows_to_wsl(case_dir))
        rdir = self._rdir(case_dir)
        # The host copy is authoritative: start from an empty directory so no stale file of a
        # previous attempt (old time steps, a graceful-stop trigger) survives into this one.
        script = f"rm -rf -- {rdir} && mkdir -p {rdir} && cp -a {src}/. {rdir}/"
        return CommandSpec(argv=self._wsl(script), cwd=str(case_dir))

    def stage_out(self, case_dir: Path) -> CommandSpec:
        dst = shlex.quote(windows_to_wsl(case_dir))
        rdir = self._rdir(case_dir)
        # copy back everything except the (large, unchanged) triSurface inputs
        script = f"set -o pipefail; cd {rdir} && tar --exclude=./constant/triSurface -cf - . | (cd {dst} && tar -xf -)"
        return CommandSpec(argv=self._wsl(script), cwd=str(case_dir))

    def push_files(self, case_dir: Path, relpaths: list[str]) -> CommandSpec:
        src = windows_to_wsl(case_dir)
        rdir = self._rdir(case_dir)
        parts = [f"cp -f {shlex.quote(src + '/' + self._rel(r))} {rdir}/{shlex.quote(self._rel(r))}" for r in relpaths]
        return CommandSpec(argv=self._wsl(" && ".join(parts)), cwd=str(case_dir))

    def remove_paths(self, case_dir: Path, globs: list[str]) -> CommandSpec:
        import re

        for g in globs:
            if not re.fullmatch(r"[A-Za-z0-9_.*-]+", g) or g in (".", ".."):
                raise ValueError(f"unsafe glob: {g}")
        rdir = self._rdir(case_dir)
        return CommandSpec(argv=self._wsl(f"cd {rdir} && rm -rf -- {' '.join(globs)}"), cwd=str(case_dir))

    def write_file(self, case_dir: Path, relpath: str, content: str) -> CommandSpec:
        rdir = self._rdir(case_dir)
        script = f"printf %s {shlex.quote(content)} > {rdir}/{shlex.quote(self._rel(relpath))}"
        return CommandSpec(argv=self._wsl(script), cwd=str(case_dir))
