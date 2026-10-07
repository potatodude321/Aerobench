"""Graceful stop and resume with a real parallel OpenFOAM run (detached runner + worker).

Stop: the abort function object makes simpleFoam write the current state and exit; the job
ends *stopped* (never failed), the latest time is on disk and the run is ingested as stopped.
Resume: continues from that time to the new end time and completes.
"""

from __future__ import annotations

import re
import time

import pytest

from aerobench.jobs.manager import JobManager
from aerobench.jobs.worker import Worker
from aerobench.services.runs import prepare_run, resume_run, run_record

pytestmark = [pytest.mark.openfoam, pytest.mark.slow]


def _latest_iteration(log_text: str) -> int:
    hits = re.findall(r"^Time = (\d+)", log_text, flags=re.M)
    return int(hits[-1]) if hits else 0


def _proc_times(case) -> list[int]:
    return sorted(int(p.name) for p in (case / "processor0").iterdir() if p.name.isdigit() and int(p.name) > 0)


def test_graceful_stop_then_resume(solved, ctx):
    p, _ = solved
    cfg = p.config.model_copy(deep=True)
    cfg.numerics.iterations = 3000  # far more than we let it run
    cfg.numerics.first_order_iterations = 10
    res = prepare_run(p, ctx, cfg, label="stop/resume")
    run_id, job_id = res["run_id"], res["job_id"]
    jm = JobManager(p)
    w = Worker(p, ctx, launch_inline=False)

    deadline = time.time() + 900
    stop_sent = False
    while time.time() < deadline:
        w.tick()
        st = jm.state(job_id)
        if st and not stop_sent:
            solve = next((s for s in st.steps if s.name == "solve"), None)
            if solve and solve.log and (p.job_dir(job_id) / solve.log).exists():
                if _latest_iteration((p.job_dir(job_id) / solve.log).read_text(errors="replace")) >= 30:
                    jm.request_stop(job_id)
                    stop_sent = True
        if stop_sent and not w.has_work():
            break
        time.sleep(1.0)
    assert stop_sent, "the solver never reached iteration 30"

    st = jm.state(job_id)
    assert st.status.value == "stopped", st.message
    assert st.stop_requested == "stop"
    solve = next(s for s in st.steps if s.name == "solve")
    assert solve.status == "stopped"
    log = (p.job_dir(job_id) / solve.log).read_text(errors="replace")
    stopped_at = _latest_iteration(log)
    assert 30 <= stopped_at < 3000
    case = p.root / run_record(p, run_id)["path"]
    written = _proc_times(case)
    assert written and written[-1] >= 30, "the graceful stop must write the current state"
    row = run_record(p, run_id)
    assert row["status"] == "stopped"

    # resume: continue to (configured 3000 would take too long) -> use a modest extension by
    # shrinking the target through extra_iterations relative to the stopped state
    out = resume_run(p, ctx, run_id, extra_iterations=written[-1] + 20 - 3000)
    assert out["target_iterations"] == written[-1] + 20
    deadline = time.time() + 900
    while time.time() < deadline:
        w.tick()
        if not w.has_work():
            break
        time.sleep(1.0)
    row = run_record(p, run_id)
    assert row["status"] == "completed", row
    st2 = jm.state(out["job_id"])
    solve2 = next(s for s in st2.steps if s.name == "solve")
    log2 = (p.job_dir(out["job_id"]) / solve2.log).read_text(errors="replace")
    first = re.findall(r"^Time = (\d+)", log2, flags=re.M)
    assert int(first[0]) == written[-1] + 1, "resume must continue from the stopped state, not restart"
    assert _latest_iteration(log2) == written[-1] + 20
    assert (case / str(written[-1] + 20)).is_dir(), "reconstructed final time"
