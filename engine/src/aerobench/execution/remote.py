"""Remote Linux/HPC provider over SSH (architecture + command generation; transport matures later).

Design: the case is staged to ``<remote_root>/<project>/<case>`` with ``rsync`` (or
``scp`` fallback) over SSH, executed directly (``mode=direct``) or submitted to Slurm
(``mode=slurm``, generated batch script), polled, and synchronised back.  No ports are
opened locally; authentication uses the user's SSH agent/keys (BUILD_SPEC §37).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from ..core.errors import Diagnostic, ErrorCategory, Severity
from .provider import CommandSpec, ExecutionProvider, MPIInfo, ProviderInfo


class RemoteSSHProvider(ExecutionProvider):
    kind = "remote-ssh"
    staging = "staged"

    def __init__(self, provider_id: str = "remote", config: Optional[dict[str, Any]] = None):
        super().__init__(provider_id, config)

    @property
    def target(self) -> str:
        user = self.config.get("user")
        host = self.config["host"]
        return f"{user}@{host}" if user else host

    def _ssh(self) -> list[str]:
        argv = ["ssh", "-o", "BatchMode=yes"]
        if self.config.get("port"):
            argv += ["-p", str(self.config["port"])]
        if self.config.get("identity_file"):
            argv += ["-i", self.config["identity_file"]]
        return argv + [self.target]

    def remote_case_dir(self, case_dir: Path) -> str:
        root = self.config.get("remote_root", "~/aerobench")
        return f"{root}/{Path(case_dir).parent.name}/{Path(case_dir).name}"

    def exec_path(self, host_path: Path) -> str:
        return self.remote_case_dir(host_path)

    def detect(self) -> ProviderInfo:
        if not self.config.get("host"):
            return ProviderInfo(id=self.id, kind=self.kind, label="Remote (SSH)", available=False, staging=self.staging,
                                problems=[Diagnostic(code="environment.remote_unconfigured", category=ErrorCategory.ENVIRONMENT,
                                                     severity=Severity.INFO, summary="No remote host is configured.")])
        return ProviderInfo(id=self.id, kind=self.kind, label=f"Remote {self.target}", available=False, staging=self.staging,
                            mpi=MPIInfo(available=True, kind="remote"),
                            problems=[Diagnostic(code="environment.remote_preview", category=ErrorCategory.ENVIRONMENT, severity=Severity.WARNING,
                                                 summary="Remote execution is in preview: commands are generated but the transport is not enabled in this build.")],
                            details={"mode": self.config.get("mode", "direct")})

    def command(self, app_argv: list[str], case_dir: Path, nprocs: int = 1) -> CommandSpec:
        rdir = self.remote_case_dir(case_dir)
        bashrc = self.config.get("bashrc", "/usr/lib/openfoam/openfoam2606/etc/bashrc")
        app = " ".join(shlex.quote(a) for a in app_argv)
        if nprocs > 1:
            app = f"mpirun -np {int(nprocs)} {app} -parallel"
        script = f"source {shlex.quote(bashrc)} >/dev/null 2>&1 && cd {shlex.quote(rdir)} && {app} -case {shlex.quote(rdir)}"
        return CommandSpec(argv=self._ssh() + [script], cwd=str(case_dir))

    def stage_in(self, case_dir: Path) -> CommandSpec:
        rdir = self.remote_case_dir(case_dir)
        return CommandSpec(argv=["rsync", "-az", "--mkpath", f"{case_dir}/", f"{self.target}:{rdir}/"], cwd=str(case_dir))

    def stage_out(self, case_dir: Path) -> CommandSpec:
        rdir = self.remote_case_dir(case_dir)
        return CommandSpec(argv=["rsync", "-az", "--exclude", "constant/triSurface", f"{self.target}:{rdir}/", f"{case_dir}/"], cwd=str(case_dir))

    def slurm_script(self, app_argv: list[str], case_dir: Path, nprocs: int, time_limit: str = "24:00:00",
                     partition: Optional[str] = None) -> str:
        rdir = self.remote_case_dir(case_dir)
        bashrc = self.config.get("bashrc", "/usr/lib/openfoam/openfoam2606/etc/bashrc")
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name=aerobench-{Path(case_dir).name}",
            f"#SBATCH --ntasks={int(nprocs)}",
            f"#SBATCH --time={time_limit}",
            f"#SBATCH --output={rdir}/slurm-%j.out",
        ]
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        app = " ".join(shlex.quote(a) for a in app_argv)
        lines += [f"source {shlex.quote(bashrc)}", f"cd {shlex.quote(rdir)}",
                  f"srun {app} -parallel -case {shlex.quote(rdir)}" if nprocs > 1 else f"{app} -case {shlex.quote(rdir)}"]
        return "\n".join(lines) + "\n"
