"""One parallel (MPI) job at a time where the MPI cannot share the computer (Microsoft MPI)."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from aerobench.execution import registry
from aerobench.execution.provider import CommandSpec, ExecutionProvider, ProviderInfo
from aerobench.jobs.mpilock import MpiLock, needs_lock
from aerobench.jobs.runner import Runner
from aerobench.jobs.spec import FoamStep, JobSpec, JobStatus, read_state, write_model

APP = r'''
import sys, time
out, seconds = sys.argv[1], float(sys.argv[2])
t0 = time.time()
time.sleep(seconds)
with open(out, "a") as fh:
    fh.write(f"{t0} {time.time()}\n")
print("End", flush=True)
'''


class OneMpiProvider(ExecutionProvider):
    kind = "fake-one-mpi"
    one_mpi_job_at_a_time = True

    def detect(self) -> ProviderInfo:
        return ProviderInfo(id=self.id, kind=self.kind, label="fake", available=True)

    def command(self, app_argv, case_dir, nprocs=1):
        return CommandSpec(argv=[sys.executable, self.config["app"], self.config["out"], app_argv[1]], cwd=str(case_dir))


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    app = tmp_path / "app.py"
    app.write_text(APP)
    monkeypatch.setitem(registry.KINDS, "fake-one-mpi", OneMpiProvider)
    lockname = f"AeroBench-test-{time.time_ns()}"
    monkeypatch.setattr("aerobench.jobs.mpilock.NAME", lockname)
    monkeypatch.setattr("aerobench.jobs.runner.MpiLock", lambda: MpiLock(lockname))
    provider = OneMpiProvider("fake", {"app": str(app), "out": str(tmp_path / "intervals.txt")})
    return tmp_path, provider, lockname


def _job(tmp_path: Path, provider, name: str, seconds: float, nprocs: int) -> Path:
    case = tmp_path / "cases" / name
    case.mkdir(parents=True)
    job_dir = tmp_path / "jobs" / name
    job_dir.mkdir(parents=True)
    write_model(job_dir / "job.json", JobSpec(job_id=name, kind="solve", project_dir=str(tmp_path), case_dir=str(case),
                                              provider=provider.serialise(),
                                              steps=[FoamStep(name="solve", app=["fakeSolver", str(seconds)], nprocs=nprocs, solver=True)]))
    return job_dir


def test_lock_is_exclusive_and_released(tmp_path):
    name = f"AeroBench-test-{time.time_ns()}"
    a, b = MpiLock(name), MpiLock(name)
    assert a.acquire(0) and not b.acquire(0.2)
    a.release()
    assert b.acquire(0.5)
    b.close()
    a.close()


def test_parallel_steps_never_overlap_and_serial_ones_do(setup):
    tmp_path, provider, _ = setup
    assert needs_lock(provider, 2) and not needs_lock(provider, 1)
    jobs = [_job(tmp_path, provider, f"p{i}", 0.6, nprocs=4) for i in range(3)]
    threads = [threading.Thread(target=lambda j=j: Runner(j).run()) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(read_state(j).status == JobStatus.COMPLETED for j in jobs)
    iv = sorted(tuple(map(float, ln.split())) for ln in (tmp_path / "intervals.txt").read_text().splitlines())
    assert len(iv) == 3 and all(iv[k + 1][0] >= iv[k][1] - 1e-3 for k in range(2))
    waited = [j for j in jobs if '"waiting_for_mpi"' in (j / "events.jsonl").read_text()]
    assert len(waited) >= 1 and "one parallel job at a time" in (waited[0] / "logs" / "00-solve.log").read_text()
    # serial steps (one process, no MPI) run side by side
    (tmp_path / "intervals.txt").unlink()
    jobs = [_job(tmp_path, provider, f"s{i}", 0.6, nprocs=1) for i in range(2)]
    threads = [threading.Thread(target=lambda j=j: Runner(j).run()) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    iv = sorted(tuple(map(float, ln.split())) for ln in (tmp_path / "intervals.txt").read_text().splitlines())
    assert iv[1][0] < iv[0][1]


def test_a_job_stopped_while_waiting_never_starts(setup):
    tmp_path, provider, lockname = setup
    holder = MpiLock(lockname)
    assert holder.acquire(0)
    job = _job(tmp_path, provider, "w", 0.1, nprocs=2)
    t = threading.Thread(target=lambda: Runner(job).run())
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and "waiting_for_mpi" not in ((job / "events.jsonl").read_text() if (job / "events.jsonl").exists() else ""):
        time.sleep(0.05)
    assert read_state(job).message.startswith("Waiting for another parallel run")
    (job / "control.json").write_text(json.dumps({"request": "stop"}))
    t.join(10)
    holder.close()
    assert read_state(job).status == JobStatus.STOPPED and not (tmp_path / "intervals.txt").exists()
