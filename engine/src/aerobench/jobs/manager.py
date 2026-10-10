"""Engine-side job management: create, launch, observe, stop, reconcile (ADR-0005)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import atomic_write_text
from ..core.ids import short_id, utc_now_iso
from ..project.store import Project
from .process import is_alive, kill_tree, launch_detached, python_executable
from .spec import JobSpec, JobState, JobStatus, Step, read_state, write_model

HEARTBEAT_STALE = 60.0


class JobManager:
    def __init__(self, project: Project):
        self.project = project
        self.db = project.db

    # ------------------------------------------------------------------ creation
    def create(
        self,
        kind: str,
        case_dir: Path,
        steps: list[Step],
        provider: dict[str, Any],
        cores: int = 1,
        target_kind: Optional[str] = None,
        target_id: Optional[str] = None,
        depends_on: Optional[str] = None,
        priority: int = 0,
        meta: Optional[dict[str, Any]] = None,
        resume_from_step: int = 0,
    ) -> str:
        jid = short_id("job")
        jdir = self.project.job_dir(jid)
        jdir.mkdir(parents=True)
        spec = JobSpec(job_id=jid, kind=kind, project_dir=str(self.project.root), case_dir=str(case_dir), provider=provider,
                       steps=steps, cores=cores, meta=meta or {}, resume_from_step=resume_from_step)
        write_model(jdir / "job.json", spec)
        write_model(jdir / "state.json", JobState(job_id=jid))
        self.db.insert("jobs", {
            "id": jid, "kind": kind, "status": JobStatus.QUEUED.value, "created_at": utc_now_iso(), "cores": cores,
            "priority": priority, "target_kind": target_kind, "target_id": target_id, "depends_on": depends_on,
            "job_dir": str(jdir.relative_to(self.project.root)),
        })
        self.db.log_event("job_created", jid, kind=kind, target=target_id)
        return jid

    # ------------------------------------------------------------------ launch
    def launch(self, job_id: str, inline: bool = False) -> None:
        jdir = self.project.job_dir(job_id)
        self.db.update("jobs", "id", job_id, {"status": JobStatus.STARTING.value, "started_at": utc_now_iso()})
        if inline:  # tests / CLI: run in this process
            from .runner import Runner

            Runner(jdir).run()
            self.refresh(job_id)
            return
        env = dict(os.environ)
        pkg_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        proc = launch_detached([python_executable(), "-m", "aerobench.jobs.runner", str(jdir)], cwd=str(jdir),
                               log_path=str(jdir / "runner.log"), env=env)
        atomic_write_text(jdir / "runner.pid", str(proc.pid))
        self.db.log_event("job_launched", job_id, pid=proc.pid)

    # ------------------------------------------------------------------ observation
    def row(self, job_id: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if r is None:
            raise AeroBenchError.make("job.unknown", ErrorCategory.PROJECT, f"Unknown job {job_id}.")
        return r

    def state(self, job_id: str) -> Optional[JobState]:
        return read_state(self.project.job_dir(job_id))

    def refresh(self, job_id: str) -> JobStatus:
        """Sync the DB row from the runner state; detect lost runners."""
        r = self.row(job_id)
        cur = JobStatus(r["status"])
        if cur.terminal:
            return cur
        st = self.state(job_id)
        if st is None:
            return cur
        status = st.status
        if status in (JobStatus.QUEUED,) and cur == JobStatus.STARTING:
            # launched but runner has not written yet; detect a runner that never started
            pidf = self.project.job_dir(job_id) / "runner.pid"
            if pidf.exists():
                pid = int(pidf.read_text().strip() or 0)
                started = r.get("started_at")
                if not is_alive(pid, None) and started and time.time() - _parse_ts(started) > 30:
                    self._mark_interrupted(job_id, st, "The job runner exited before starting (see runner.log).")
                    return JobStatus.INTERRUPTED
            return cur
        if not status.terminal:
            alive = is_alive(st.runner_pid, st.runner_create_time)
            stale = st.heartbeat is not None and time.time() - st.heartbeat > HEARTBEAT_STALE
            if not alive and (stale or st.heartbeat is None or not alive):
                self._mark_interrupted(job_id, st, "The job runner stopped unexpectedly (computer restart or crash).")
                return JobStatus.INTERRUPTED
        changes: dict[str, Any] = {"status": status.value, "message": st.message}
        if status.terminal:
            changes["ended_at"] = st.ended_at or utc_now_iso()
        self.db.update("jobs", "id", job_id, changes)
        return status

    def _mark_interrupted(self, job_id: str, st: JobState, msg: str) -> None:
        # the runner is gone; its solver (own session/process group) may still be running and
        # would compete with a retry or resume for cores and time directories
        if st.child_pid and is_alive(st.child_pid, st.child_create_time):
            try:
                kill_tree(st.child_pid, timeout=10.0, graceful=True)
                msg += " The orphaned solver process was stopped."
            except Exception:  # noqa: BLE001
                msg += " An orphaned solver process could not be stopped (PID %d)." % st.child_pid
        st.status = JobStatus.INTERRUPTED
        st.message = msg
        st.ended_at = utc_now_iso()
        write_model(self.project.job_dir(job_id) / "state.json", st)
        self.db.update("jobs", "id", job_id, {"status": JobStatus.INTERRUPTED.value, "message": msg, "ended_at": st.ended_at})
        self.db.log_event("job_interrupted", job_id, message=msg)

    def reconcile(self) -> list[str]:
        """Called at engine start: refresh every non-terminal job."""
        changed = []
        for r in self.db.all("SELECT id, status FROM jobs WHERE status NOT IN ('completed','failed','stopped','interrupted','cancelled')"):
            if r["status"] == JobStatus.QUEUED.value:
                continue
            new = self.refresh(r["id"])
            if new.value != r["status"]:
                changed.append(r["id"])
        return changed

    # ------------------------------------------------------------------ control
    def request_stop(self, job_id: str, force: bool = False) -> None:
        r = self.row(job_id)
        if r["status"] == JobStatus.QUEUED.value:
            self.db.update("jobs", "id", job_id, {"status": JobStatus.CANCELLED.value, "ended_at": utc_now_iso(), "message": "Cancelled before start."})
            return
        atomic_write_text(self.project.job_dir(job_id) / "control.json", json.dumps({"request": "kill" if force else "stop", "t": utc_now_iso()}))
        self.db.log_event("job_stop_requested", job_id, force=force)

    def request_finish(self, job_id: str) -> None:
        """Let the running solver write its current state and end normally; the job then
        completes its remaining steps (used when a run has converged)."""
        atomic_write_text(self.project.job_dir(job_id) / "control.json", json.dumps({"request": "finish", "t": utc_now_iso()}))
        self.db.log_event("job_finish_requested", job_id)

    def list(self, statuses: Optional[list[str]] = None) -> list[dict[str, Any]]:
        if statuses:
            q = "SELECT * FROM jobs WHERE status IN (%s) ORDER BY created_at" % ",".join("?" * len(statuses))
            return self.db.all(q, tuple(statuses))
        return self.db.all("SELECT * FROM jobs ORDER BY created_at")

    def log_tail(self, job_id: str, step_index: Optional[int] = None, offset: int = 0, max_bytes: int = 256_000) -> dict[str, Any]:
        st = self.state(job_id)
        if st is None:
            return {"text": "", "offset": 0, "step": None}
        idx = st.step_index if step_index is None else step_index
        if idx >= len(st.steps) or not st.steps[idx].log:
            return {"text": "", "offset": 0, "step": idx}
        p = self.project.job_dir(job_id) / st.steps[idx].log
        if not p.exists():
            return {"text": "", "offset": 0, "step": idx}
        size = p.stat().st_size
        start = offset if 0 <= offset <= size else max(0, size - max_bytes)
        if size - start > max_bytes:
            start = size - max_bytes
        with open(p, "rb") as fh:
            fh.seek(start)
            text = fh.read(size - start).decode("utf-8", errors="replace")
        return {"text": text, "offset": size, "step": idx, "log": st.steps[idx].log}

    def commands(self, job_id: str) -> list[dict[str, Any]]:
        """Exact commands executed (Expert Mode)."""
        p = self.project.job_dir(job_id) / "events.jsonl"
        out = []
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("kind") == "command":
                    out.append(e)
        return out


def _parse_ts(s: str) -> float:
    import datetime as dt

    return dt.datetime.fromisoformat(s).timestamp()
