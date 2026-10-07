"""Detached job runner: ``python -m aerobench.jobs.runner <job-dir>``.

The runner is the single writer of the job directory.  It survives the GUI and the
engine service (ADR-0005).  It never parses results — it executes steps, keeps the raw
logs, writes state + events, honours stop/kill requests, and classifies failures.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from ..core.errors import Diagnostic, ErrorCategory
from ..core.fs import atomic_write_text
from ..core.ids import utc_now_iso
from ..execution.registry import provider_from_dict
from ..foam.case.builder import STOP_FO_NAME
from ..foam.logs.classify import classify
from .process import create_time, kill_tree, popen_kwargs
from .spec import (
    CopyStep,
    DictSetStep,
    FoamStep,
    JobSpec,
    JobState,
    JobStatus,
    LinkMeshStep,
    RemoveStep,
    StageStep,
    StepState,
    read_spec,
    read_state,
    write_model,
)

POLL = 0.5
HEARTBEAT = 5.0
GRACE_STOP_SECONDS = 600  # graceful stop: wait for the solver to write and exit
MIRROR_TIMEOUT = 120  # mirroring a small edit/trigger into a staged copy
SALVAGE_TIMEOUT = 4 * 3600  # copying a staged case back after a stop/failure


class Runner:
    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self.spec: JobSpec = read_spec(self.job_dir)
        self.case_dir = Path(self.spec.case_dir)
        self.provider = provider_from_dict(self.spec.provider)
        prev = read_state(self.job_dir)
        self.state = JobState(job_id=self.spec.job_id, steps=[StepState(name=s.name) for s in self.spec.steps])
        if prev and self.spec.resume_from_step:
            # keep history of completed steps when resuming
            for i, st in enumerate(prev.steps[: self.spec.resume_from_step]):
                self.state.steps[i] = st
        (self.job_dir / "logs").mkdir(exist_ok=True)
        self._last_hb = 0.0
        # Staged providers (WSL, remote) run applications on a copy of the case.  While that
        # copy is "live" (staged in, not yet staged out) host-side edits must be mirrored into
        # it, and it must be copied back however the job ends.
        self._staged_live = False
        # A stop/kill took effect.  The on-disk status stays STOPPING (not terminal) until the
        # staged copy has been salvaged, so nothing ingests or resumes a half-copied case.
        self._stopped = False
        if self.provider.staging == "staged":
            for st_def, st_state in zip(self.spec.steps[: self.spec.resume_from_step], self.state.steps):
                if isinstance(st_def, StageStep) and st_state.status == "completed":
                    self._staged_live = st_def.direction == "in"

    # ----------------------------------------------------------------- state & events
    def save(self) -> None:
        self.state.heartbeat = time.time()
        write_model(self.job_dir / "state.json", self.state)

    def event(self, kind: str, **data) -> None:
        rec = {"t": utc_now_iso(), "kind": kind, **data}
        with open(self.job_dir / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def control(self) -> Optional[str]:
        p = self.job_dir / "control.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("request")
        except (json.JSONDecodeError, OSError):
            return None

    # ----------------------------------------------------------------- main
    def run(self) -> int:
        self.state.status = JobStatus.RUNNING
        self.state.runner_pid = os.getpid()
        self.state.runner_create_time = create_time(os.getpid())
        self.state.started_at = utc_now_iso()
        self.save()
        self.event("job_started", pid=os.getpid(), provider=self.spec.provider.get("id"))
        try:
            for i in range(self.spec.resume_from_step, len(self.spec.steps)):
                step = self.spec.steps[i]
                req = self.control()
                if req in ("stop", "kill"):
                    self.state.status = JobStatus.STOPPING
                    self.state.stop_requested = req
                    self.save()
                    self._salvage("stopped")
                    self._finish(JobStatus.STOPPED, f"Stopped before step '{step.name}'.")
                    return 0
                self.state.step_index = i
                ok = self._run_step(i, step)
                if self._stopped:
                    how = "forced" if self.state.stop_requested == "kill" else "graceful (latest state written)"
                    self._salvage("stopped")
                    self._finish(JobStatus.STOPPED, f"Stopped by request during '{step.name}' — {how}.")
                    return 0
                if not ok and not step.optional:
                    self._salvage("failed")
                    self._finish(JobStatus.FAILED, f"Step '{step.name}' failed.")
                    return 1
            self._finish(JobStatus.COMPLETED, "Completed.")
            return 0
        except Exception as exc:  # noqa: BLE001
            self.state.diagnostics.append(Diagnostic(code="internal.runner", category=ErrorCategory.INTERNAL,
                                                     summary="The job runner failed unexpectedly.", details=traceback.format_exc()))
            try:
                self._salvage("failed")
            except Exception:  # noqa: BLE001
                pass
            self._finish(JobStatus.FAILED, f"Runner error: {exc}")
            return 2

    # ----------------------------------------------------------------- staged copies
    def _plain(self, argv: list[str], env: dict, cwd: str, log: Path, timeout: float) -> int:
        """Run a short helper command to completion, ignoring stop requests (used for
        mirroring and salvage, which must not be interrupted half-way)."""
        with open(log, "ab") as fh:
            fh.write(f"# aerobench: {utc_now_iso()} :: {' '.join(argv)}\n".encode())
            fh.flush()
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                        **popen_kwargs(detached=False))
            except OSError as exc:
                fh.write(f"# aerobench: could not start: {exc}\n".encode())
                return 127
        self.state.child_pid = proc.pid
        self.state.child_create_time = create_time(proc.pid)
        self.save()
        deadline = time.time() + timeout
        while proc.poll() is None:
            if time.time() > deadline:
                kill_tree(proc.pid, graceful=False)
                proc.wait()
                break
            if time.time() - self._last_hb > HEARTBEAT:
                self._last_hb = time.time()
                self.save()
            time.sleep(min(POLL, 0.1))
        self.state.child_pid = None
        self.save()
        return proc.returncode if proc.returncode is not None else -9

    def _mirror(self, spec, i: int, st: StepState, what: str) -> bool:
        """Apply a host-side change to the live staged copy (no-op for in-place providers)."""
        if not self._staged_live:
            return True
        if spec is None:
            # a staged provider that cannot apply the edit would let the solver run on stale
            # input (e.g. the wrong schemes or endTime): fail the step instead
            st.diagnostics.append(Diagnostic(code="environment.staging_unsupported", category=ErrorCategory.ENVIRONMENT,
                                             summary=f"The execution provider cannot apply '{st.name}' to its staged copy of the case.",
                                             action="Use another execution provider for this job."))
            return False
        log = self.job_dir / "logs" / f"{i:02d}-{st.name}.log"
        st.log = str(log.relative_to(self.job_dir))
        st.command = spec.argv
        rc = self._plain(spec.argv, {**os.environ, **spec.env}, spec.cwd or str(self.case_dir), log, MIRROR_TIMEOUT)
        self.event("staged_mirror", step=st.name, what=what, exit_code=rc)
        if rc != 0:
            st.diagnostics.append(Diagnostic(code="environment.staging_failed", category=ErrorCategory.ENVIRONMENT,
                                             summary=f"Could not apply '{st.name}' to the staged copy of the case.",
                                             details=f"exit code {rc}; see {st.log}"))
        st.exit_code = rc
        return rc == 0

    def _salvage(self, why: str) -> None:
        """Copy a live staged case back to the host after a stop or failure, so the latest
        written time (needed to resume) and partial results/logs are not stranded."""
        if not self._staged_live:
            return
        spec = self.provider.stage_out(self.case_dir)
        if spec is None:
            self._staged_live = False
            return
        log = self.job_dir / "logs" / "salvage-stage_out.log"
        self.state.message = "Copying the results back from the staged copy of the case…"
        self.save()
        self.event("staged_salvage_started", reason=why)
        rc = self._plain(spec.argv, {**os.environ, **spec.env}, spec.cwd or str(self.case_dir), log, SALVAGE_TIMEOUT)
        self.event("staged_salvage_finished", reason=why, exit_code=rc)
        if rc == 0:
            self._staged_live = False
        else:
            self.state.diagnostics.append(Diagnostic(
                code="environment.staging_failed", category=ErrorCategory.ENVIRONMENT,
                summary="The staged copy of the case could not be copied back after the job ended.",
                details=f"exit code {rc}; see logs/salvage-stage_out.log",
                action="The latest results are still in the staged copy. Copy them back before resuming or re-running "
                       "this run (a new stage-in refuses to overwrite newer staged results) by running this command "
                       f"in a terminal:\n{spec.display()}"))

    def _finish(self, status: JobStatus, msg: str) -> None:
        self.state.status = status
        self.state.message = msg
        self.state.ended_at = utc_now_iso()
        self.state.child_pid = None
        self.save()
        self.event("job_finished", status=status.value, message=msg)

    def _run_step(self, i: int, step) -> bool:
        st = self.state.steps[i]
        st.status = "running"
        st.started_at = utc_now_iso()
        self.save()
        self.event("step_started", step=step.name, index=i)
        try:
            if isinstance(step, FoamStep):
                ok = self._foam(i, step, st)
            elif isinstance(step, CopyStep):
                shutil.copyfile(self.case_dir / step.src, self.case_dir / step.dst)
                ok = self._mirror(self.provider.push_files(self.case_dir, [step.dst]) if self._staged_live else None, i, st, step.dst)
            elif isinstance(step, RemoveStep):
                for pat in step.globs:
                    for p in glob.glob(os.path.join(glob.escape(str(self.case_dir)), pat)):
                        pp = Path(p).resolve()
                        if self.case_dir.resolve() not in pp.parents:
                            raise RuntimeError(f"refusing to remove outside the case: {pp}")
                        shutil.rmtree(pp) if pp.is_dir() else pp.unlink()
                ok = self._mirror(self.provider.remove_paths(self.case_dir, step.globs) if self._staged_live else None, i, st,
                                  " ".join(step.globs))
            elif isinstance(step, DictSetStep):
                from ..foam.ast import from_json
                from ..foam.parser import parse, strip_header
                from ..foam.writer import write_foam_file

                path = self.case_dir / step.file
                hdr, body = strip_header(parse(path.read_text()))
                body.set_path(step.key_path, from_json(step.value))
                obj = hdr.get("object").value if hdr is not None and hdr.get("object") is not None else path.name
                atomic_write_text(path, write_foam_file(body, obj, location=str(Path(step.file).parent)))
                ok = self._mirror(self.provider.push_files(self.case_dir, [step.file]) if self._staged_live else None, i, st, step.file)
            elif isinstance(step, LinkMeshStep):
                from ..core.fs import link_or_copy

                src = Path(step.src_case) / "constant" / "polyMesh"
                dst = self.case_dir / "constant" / "polyMesh"
                if not (src / "faces").exists() and not (src / "faces.gz").exists():
                    raise RuntimeError(f"source mesh not found: {src}")
                if dst.exists():
                    shutil.rmtree(dst)
                modes = set()
                for f in src.rglob("*"):
                    if f.is_file():
                        modes.add(link_or_copy(f, dst / f.relative_to(src)))
                self.event("mesh_linked", src=str(src), modes=sorted(modes))
                ok = True
            elif isinstance(step, StageStep):
                spec = self.provider.stage_in(self.case_dir) if step.direction == "in" else self.provider.stage_out(self.case_dir)
                if spec is None:
                    ok = True
                else:
                    log = self.job_dir / "logs" / f"{i:02d}-{step.name}.log"
                    st.log = str(log.relative_to(self.job_dir))
                    st.command = spec.argv
                    rc = self._exec(spec.argv, {**os.environ, **spec.env}, spec.cwd or str(self.case_dir), log, solver=False)
                    st.exit_code = rc
                    ok = rc == 0
                if step.direction == "in":
                    # Only a complete stage-in is live: nothing has run on a failed (possibly
                    # truncated) copy, and copying it back could damage the host case.
                    self._staged_live = ok and spec is not None
                elif ok:
                    self._staged_live = False
            else:
                raise RuntimeError(f"unknown step type {type(step)}")
        except Exception as exc:  # noqa: BLE001
            st.diagnostics.append(Diagnostic(code="internal.step", category=ErrorCategory.INTERNAL, summary=f"Step '{step.name}' raised an error.",
                                             details=f"{exc}\n{traceback.format_exc()}"))
            ok = False
        if self._stopped:
            st.status = "stopped"
        else:
            st.status = "completed" if ok else ("skipped" if step.optional else "failed")
        st.ended_at = utc_now_iso()
        self.save()
        self.event("step_finished", step=step.name, index=i, status=st.status, exit_code=st.exit_code)
        return ok

    def _foam(self, i: int, step: FoamStep, st: StepState) -> bool:
        spec = self.provider.command(step.app, self.case_dir, step.nprocs)
        log = self.job_dir / "logs" / f"{i:02d}-{step.name}.log"
        st.log = str(log.relative_to(self.job_dir))
        st.command = spec.argv
        env = dict(os.environ)
        env.update(spec.env)
        for k in spec.unset_env:
            env.pop(k, None)
        self.event("command", step=step.name, argv=spec.argv, cwd=spec.cwd)
        # graceful-stop trigger must not exist from a previous attempt
        trig = self.case_dir / STOP_FO_NAME
        if trig.exists():
            trig.unlink()
        rc = self._exec(spec.argv, env, spec.cwd or str(self.case_dir), log, solver=step.solver)
        st.exit_code = rc
        text = log.read_text(errors="replace") if log.exists() else ""
        if self._stopped:
            return True
        ok = rc == 0 and (not step.require_end or _has_end(text))
        if not ok:
            diags = classify(text, step.stage, rc, {"step": step.name})
            if rc == 0 and not diags:
                diags = [Diagnostic(code=f"{step.stage or 'step'}.incomplete", category=ErrorCategory.SOLVER,
                                    summary=f"{step.app[0]} exited without completing (no 'End').", details=text[-3000:])]
            st.diagnostics.extend(diags)
            self.state.diagnostics.extend(diags)
        return ok

    def _trigger_staged(self) -> bool:
        """Write the graceful-stop trigger into the staged copy the solver is reading."""
        spec = self.provider.write_file(self.case_dir, STOP_FO_NAME, "action=writeNow\n")
        if spec is None:
            return False  # cannot reach the copy: the caller terminates the solver instead
        try:
            out = subprocess.run(spec.argv, cwd=spec.cwd or str(self.case_dir), env={**os.environ, **spec.env},
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=MIRROR_TIMEOUT)
            return out.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _exec(self, argv: list[str], env: dict, cwd: str, log: Path, solver: bool) -> int:
        with open(log, "ab") as fh:
            fh.write(f"# aerobench: {utc_now_iso()} :: {' '.join(argv)}\n".encode())
            fh.flush()
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                    **popen_kwargs(detached=False))
        self.state.child_pid = proc.pid
        self.state.child_create_time = create_time(proc.pid)
        self.save()
        stop_sent_at: Optional[float] = None
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.time()
            req = self.control()
            if req == "kill":
                self.event("kill_requested")
                kill_tree(proc.pid, graceful=False)
                proc.wait()
                self._stopped = True
                self.state.status = JobStatus.STOPPING
                self.state.stop_requested = "kill"
                self.state.child_pid = None
                self.save()
                return proc.returncode if proc.returncode is not None else -9
            if req == "stop" and stop_sent_at is None:
                self.state.stop_requested = "stop"
                self.state.status = JobStatus.STOPPING
                stop_sent_at = now
                if solver:
                    # OpenFOAM abort function object: stop and write the current state
                    atomic_write_text(self.case_dir / STOP_FO_NAME, "action=writeNow\n")
                    if self._staged_live and not self._trigger_staged():
                        self.event("graceful_stop_unavailable", mechanism="terminate")
                        kill_tree(proc.pid, graceful=True)
                    else:
                        self.event("graceful_stop_requested", mechanism="abort function object")
                else:
                    self.event("stop_requested", mechanism="terminate")
                    kill_tree(proc.pid, graceful=True)
                self.save()
            if stop_sent_at is not None and now - stop_sent_at > GRACE_STOP_SECONDS:
                self.event("graceful_stop_timeout")
                kill_tree(proc.pid, graceful=False)
            if now - self._last_hb > HEARTBEAT:
                self._last_hb = now
                self.save()
            time.sleep(POLL)
        if stop_sent_at is not None:
            self._stopped = True  # status stays STOPPING until run() has salvaged and finished
        self.state.child_pid = None
        self.save()
        return rc


def _has_end(text: str) -> bool:
    tail = text[-4000:]
    return "\nEnd\n" in tail or tail.rstrip().endswith("\nEnd") or "\nEnd\r\n" in tail


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m aerobench.jobs.runner <job-dir>", file=sys.stderr)
        return 64
    runner = Runner(Path(argv[0]))
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
