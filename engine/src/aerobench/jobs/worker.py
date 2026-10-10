"""Per-project worker: scheduling, dependency handling, ingestion and study driving.

Runs detached (``python -m aerobench.jobs.worker <project-dir>``) so queued work, studies
and optimisations continue unattended after the GUI closes.  Exactly one worker per
project holds ``.worker.lock``; it exits after ``idle_timeout`` seconds without work.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.fs import atomic_write_text
from ..core.ids import utc_now_iso
from ..project.store import Project
from .manager import JobManager
from .process import create_time, is_alive, launch_detached, python_executable

LOCK = ".worker.lock"
TERMINAL = ("completed", "failed", "stopped", "interrupted", "cancelled")
INGEST_ATTEMPTS = 8  # about 16 s at the default 2 s tick


def _ingestors() -> dict[str, Callable]:
    from ..services.mesh import ingest_mesh
    from ..services.runs import ingest_run

    from ..services.structures import ingest_structure

    return {"mesh": lambda p, tid: ingest_mesh(p, tid), "solve": lambda p, tid: ingest_run(p, tid),
            "fea": lambda p, tid: ingest_structure(p, tid)}


def lock_holder(root: Path) -> Optional[dict]:
    p = Path(root) / LOCK
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return d if is_alive(d.get("pid"), d.get("create_time")) else None


def acquire_lock(root: Path) -> bool:
    p = Path(root) / LOCK
    holder = lock_holder(root)
    if holder and holder.get("pid") != os.getpid():
        return False
    atomic_write_text(p, json.dumps({"pid": os.getpid(), "create_time": create_time(os.getpid()), "started": utc_now_iso()}))
    # re-check (best effort against a simultaneous start)
    time.sleep(0.05)
    d = json.loads(p.read_text())
    return d.get("pid") == os.getpid()


def release_lock(root: Path) -> None:
    p = Path(root) / LOCK
    try:
        d = json.loads(p.read_text())
        if d.get("pid") == os.getpid():
            p.unlink()
    except (OSError, json.JSONDecodeError):
        pass


class Worker:
    def __init__(self, project: Project, ctx=None, launch_inline: bool = False):
        from ..services.context import AppContext

        self.project = project
        self.ctx = ctx or AppContext()
        self.jm = JobManager(project)
        self.launch_inline = launch_inline
        self.db = project.db
        self._ingest_attempts: dict[str, int] = {}
        self._conv_checked: dict[str, float] = {}

    # ------------------------------------------------------------------ one scheduling pass
    def tick(self) -> dict[str, Any]:
        report: dict[str, Any] = {"refreshed": [], "ingested": [], "launched": [], "cancelled": [], "studies": []}
        for j in self.jm.list(["starting", "running", "stopping"]):
            new = self.jm.refresh(j["id"])
            if new.value != j["status"]:
                report["refreshed"].append(j["id"])
        ing = _ingestors()
        for j in self.db.all("SELECT * FROM jobs WHERE ingested = 0 AND status IN ('completed','failed','stopped','interrupted','cancelled') "
                             "ORDER BY ended_at"):
            fn = ing.get(j["kind"])
            try:
                if fn and j["target_id"]:
                    fn(self.project, j["target_id"])
                self.db.update("jobs", "id", j["id"], {"ingested": 1})
                report["ingested"].append(j["id"])
                self._ingest_attempts.pop(j["id"], None)
            except Exception as exc:  # noqa: BLE001 — never let one bad result stop the queue
                # Result files can still be held open for a moment after the solver exits
                # (antivirus scanners, file indexers and network drives on Windows): retry for
                # a while before declaring the results unreadable.
                n = self._ingest_attempts.get(j["id"], 0) + 1
                self._ingest_attempts[j["id"]] = n
                tb = traceback.format_exc()
                if n < INGEST_ATTEMPTS:
                    self.db.log_event("ingest_retry", j["id"], attempt=n, error=tb[-1500:])
                    continue
                self.db.update("jobs", "id", j["id"], {"ingested": 1, "message": "ingestion error: " + tb[-1500:]})
                self.db.log_event("ingest_error", j["id"], error=tb[-3000:])
                self._finalise_unreadable(j, exc)
        self._auto_stop_converged(report)
        try:
            from ..studies.driver import drive_all

            report["studies"] = drive_all(self.project, self.ctx)
        except Exception:  # noqa: BLE001
            self.db.log_event("study_driver_error", None, error=traceback.format_exc()[-3000:])
        self._schedule(report)
        return report

    def _auto_stop_converged(self, report: dict) -> None:
        """Runs set to stop when converged: request a graceful stop (current state written) once
        every convergence criterion is met. Checked at most every 20 s per run."""
        now = time.time()
        for j in self.jm.list(["running"]):
            if j["kind"] != "solve" or not j["target_id"] or now - self._conv_checked.get(j["id"], 0) < 20:
                continue
            self._conv_checked[j["id"]] = now
            try:
                row = self.db.one("SELECT config_snapshot_json FROM runs WHERE id = ?", (j["target_id"],))
                snap = json.loads(row["config_snapshot_json"]) if row and row["config_snapshot_json"] else {}
                if not (snap.get("numerics") or {}).get("stop_when_converged"):
                    continue
                from ..services.progress import run_progress

                pr = run_progress(self.project, j["target_id"])
                conv = pr.get("convergence") or {}
                if conv.get("status") == "converged" and conv.get("total") and conv.get("met") == conv.get("total"):
                    self.jm.request_finish(j["id"])
                    self.db.update("runs", "id", j["target_id"], {"notes": "Ended early: all convergence criteria were met."})
                    self.db.log_event("auto_stop_converged", j["target_id"], iteration=pr.get("iteration"))
                    report.setdefault("auto_stopped", []).append(j["id"])
            except Exception:  # noqa: BLE001 - never let the check disturb scheduling
                self.db.log_event("auto_stop_check_error", j["id"], error=traceback.format_exc()[-1500:])

    def _finalise_unreadable(self, job: dict, exc: Exception) -> None:
        """A finished job whose outputs could not be read must not leave its run or mesh
        'running' for ever (studies would wait on it)."""
        from ..core.errors import Diagnostic, ErrorCategory

        diag = Diagnostic(code="results.unreadable", category=ErrorCategory.INTERNAL,
                          summary="The results of this job could not be read.",
                          likely_cause="Output files are incomplete or damaged (e.g. the solver was killed while writing).",
                          action="Use 'Re-read results from the case' in the Run page menu; if it fails again, reproduce the run.",
                          details=f"{type(exc).__name__}: {exc}").model_dump(mode="json")
        try:
            if job["kind"] == "solve" and job["target_id"]:
                self.db.update("runs", "id", job["target_id"], {"status": "failed", "diagnostics_json": [diag]})
            elif job["kind"] == "mesh" and job["target_id"]:
                self.db.update("mesh_revisions", "id", job["target_id"], {"status": "failed"})
        except Exception:  # noqa: BLE001
            pass

    def _dependency_state(self, dep_id: Optional[str]) -> str:
        """'ok' | 'wait' | 'failed'"""
        if not dep_id:
            return "ok"
        d = self.db.one("SELECT * FROM jobs WHERE id = ?", (dep_id,))
        if d is None:
            return "failed"
        if d["status"] in ("failed", "stopped", "interrupted", "cancelled"):
            return "failed"
        if d["status"] != "completed" or not d["ingested"]:
            return "wait"
        if d["kind"] == "mesh":
            m = self.db.one("SELECT status FROM mesh_revisions WHERE id = ?", (d["target_id"],))
            if not m or m["status"] != "completed":
                return "failed"
        return "ok"

    def _schedule(self, report: dict) -> None:
        busy = sum(j["cores"] for j in self.jm.list(["starting", "running", "stopping"]))
        budget = self.ctx.max_cores() - busy
        for j in self.db.all("SELECT * FROM jobs WHERE status = 'queued' ORDER BY priority DESC, created_at"):
            dep = self._dependency_state(j["depends_on"])
            if dep == "failed":
                # ingested=1: the run record is finalised right here with the reason
                self.db.update("jobs", "id", j["id"], {"status": "cancelled", "ended_at": utc_now_iso(), "ingested": 1,
                                                       "message": f"Cancelled: prerequisite job {j['depends_on']} did not complete successfully."})
                if j["kind"] == "solve" and j["target_id"]:
                    from ..core.errors import Diagnostic, ErrorCategory, Severity

                    d = self.db.one("SELECT status FROM jobs WHERE id = ?", (j["depends_on"],))
                    if d and d["status"] in ("cancelled", "stopped"):
                        diag = Diagnostic(code="mesh.cancelled", category=ErrorCategory.MESH, severity=Severity.INFO,
                                          summary="Meshing was cancelled, so the run did not start.",
                                          action="Generate the mesh again, or start a new run, which meshes first.")
                        status = "cancelled"
                    else:
                        diag = Diagnostic(code="mesh.failed", category=ErrorCategory.MESH, summary="Meshing failed, so the run could not start.",
                                          action="Open the Mesh page for the meshing diagnostics.")
                        status = "failed"
                    self.db.update("runs", "id", j["target_id"], {"status": status, "diagnostics_json": [diag.model_dump(mode="json")]})
                report["cancelled"].append(j["id"])
                continue
            if dep == "wait":
                continue
            if j["cores"] > budget and busy > 0:
                continue  # wait for cores; never oversubscribe (a lone oversized job may run alone)
            self.jm.launch(j["id"], inline=self.launch_inline)
            if j["target_kind"] == "run":
                self.db.update("runs", "id", j["target_id"], {"status": "running"})
            elif j["target_kind"] == "mesh":
                self.db.update("mesh_revisions", "id", j["target_id"], {"status": "running"})
            report["launched"].append(j["id"])
            budget -= j["cores"]
            busy += j["cores"]
            if self.launch_inline:
                # inline mode runs to completion synchronously; ingest promptly on next tick
                pass

    def has_work(self) -> bool:
        active = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','starting','running','stopping') OR "
                             "(ingested = 0 AND status IN ('completed','failed','stopped','interrupted','cancelled'))")["n"]
        studies = self.db.one("SELECT COUNT(*) AS n FROM studies WHERE status = 'running'")["n"]
        return bool(active or studies)

    def run_until_idle(self, poll: float = 2.0, idle_timeout: float = 300.0, max_seconds: Optional[float] = None) -> None:
        start = time.time()
        idle_since: Optional[float] = None
        while True:
            self.tick()
            if self.has_work():
                idle_since = None
            else:
                idle_since = idle_since or time.time()
                if time.time() - idle_since >= idle_timeout:
                    return
            if max_seconds is not None and time.time() - start > max_seconds:
                return
            time.sleep(poll)


def ensure_worker_process(root: Path) -> Optional[int]:
    """Start a detached worker for ``root`` unless one is alive.  Returns its PID (if started)."""
    if lock_holder(root):
        return None
    env = dict(os.environ)
    pkg_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    log = Path(root) / "jobs" / "worker.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = launch_detached([python_executable(), "-m", "aerobench.jobs.worker", str(root)], cwd=str(root), log_path=str(log), env=env)
    return proc.pid


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0])
    if not acquire_lock(root):
        print("another worker holds the lock", file=sys.stderr)
        return 0
    try:
        from ..services.context import AppContext

        ctx = AppContext()
        project = Project.open(root)
        Worker(project, ctx).run_until_idle(idle_timeout=float(ctx.settings.worker_idle_timeout_s))
        return 0
    finally:
        release_lock(root)


if __name__ == "__main__":
    raise SystemExit(main())
