"""Staged execution (WSL / remote): the solver runs on a *copy* of the case.

These tests drive the real job runner with a fake staged provider whose "remote" copy is
a local directory, and check that host-side edits between solver steps reach the copy, that
a graceful stop reaches the solver through the copy, and that results come back to the host
however the job ends (completed, stopped, failed).
"""

from __future__ import annotations

import json
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

from aerobench.execution import registry
from aerobench.execution.provider import CommandSpec, ExecutionProvider, ProviderInfo, staged_case_key
from aerobench.execution.wsl import WSLProvider
from aerobench.jobs.runner import Runner
from aerobench.jobs.spec import CopyStep, DictSetStep, FoamStep, JobSpec, JobStatus, RemoveStep, StageStep, read_state, write_model

HELPER = r'''
import os, re, shutil, sys, time
from pathlib import Path

op, args = sys.argv[1], sys.argv[2:]
if op == "copytree":          # src dst (clean mirror)
    src, dst = Path(args[0]), Path(args[1])
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
elif op == "copyback":        # src dst [seconds] (merge, like tar -x; optionally slow)
    if len(args) > 2:
        time.sleep(float(args[2]))
    shutil.copytree(Path(args[0]), Path(args[1]), dirs_exist_ok=True)
elif op == "push":            # host remote rel...
    for rel in args[2:]:
        shutil.copyfile(Path(args[0]) / rel, Path(args[1]) / rel)
elif op == "remove":          # remote glob...
    for g in args[1:]:
        for p in Path(args[0]).glob(g):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
elif op == "write":           # remote rel content
    (Path(args[0]) / args[1]).write_text(args[2])
elif op == "solve":           # remote [fail_at] — a fake steady solver
    case = Path(args[0])
    fail_at = int(args[1]) if len(args) > 1 else -1
    text = (case / "system" / "controlDict").read_text()
    end = int(float(re.search(r"\bendTime\s+([-+0-9.eE]+)\s*;", text).group(1)))
    (case / "seen.txt").write_text(f"{end} {(case / 'system' / 'fvSchemes').read_text().strip()}")
    print("Starting time loop", flush=True)
    for t in range(1, end + 1):
        time.sleep(0.02)
        (case / str(t)).mkdir(exist_ok=True)
        (case / str(t) / "U").write_text(f"t={t}")
        if t == fail_at:
            print("--> FOAM FATAL ERROR: fake failure", flush=True)
            sys.exit(1)
        if (case / "aerobenchStop").exists():
            print(f"stopping at {t}", flush=True)
            break
    print("End", flush=True)
'''


class FakeStagedProvider(ExecutionProvider):
    kind = "fake-staged"
    staging = "staged"

    def _remote(self, case_dir: Path) -> Path:
        return Path(self.config["remote_root"]) / staged_case_key(case_dir)

    def _py(self, *args: str) -> list[str]:
        return [sys.executable, self.config["helper"], *args]

    def detect(self) -> ProviderInfo:
        return ProviderInfo(id=self.id, kind=self.kind, label="fake", available=True, staging=self.staging)

    def command(self, app_argv, case_dir, nprocs=1):
        assert app_argv[0] == "fakeSolver"
        return CommandSpec(argv=self._py("solve", str(self._remote(case_dir)), *app_argv[1:]), cwd=str(case_dir))

    def stage_in(self, case_dir):
        return CommandSpec(argv=self._py("copytree", str(case_dir), str(self._remote(case_dir))))

    def stage_out(self, case_dir):
        slow = [str(self.config["slow_copyback"])] if self.config.get("slow_copyback") else []
        return CommandSpec(argv=self._py("copyback", str(self._remote(case_dir)), str(case_dir), *slow))

    def push_files(self, case_dir, relpaths):
        return CommandSpec(argv=self._py("push", str(case_dir), str(self._remote(case_dir)), *relpaths))

    def remove_paths(self, case_dir, globs):
        return CommandSpec(argv=self._py("remove", str(self._remote(case_dir)), *globs))

    def write_file(self, case_dir, relpath, content):
        return CommandSpec(argv=self._py("write", str(self._remote(case_dir)), relpath, content))


CONTROL = """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object controlDict;
}
application simpleFoam;
startTime 0;
endTime 1000;
writeInterval 1;
"""


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    helper = tmp_path / "helper.py"
    helper.write_text(HELPER)
    monkeypatch.setitem(registry.KINDS, "fake-staged", FakeStagedProvider)
    case = tmp_path / "proj" / "runs" / "run-1"
    (case / "system").mkdir(parents=True)
    (case / "0").mkdir()
    (case / "system" / "controlDict").write_text(CONTROL)
    (case / "system" / "fvSchemes").write_text("second-order")
    (case / "system" / "fvSchemes.firstOrder").write_text("first-order")
    (case / "aerobenchStop").write_text("stale trigger from an earlier attempt")
    remote_root = tmp_path / "remote"
    provider = FakeStagedProvider("fake", {"remote_root": str(remote_root), "helper": str(helper)})
    return case, provider, provider._remote(case)


def _job(tmp_path: Path, case: Path, provider, steps, name="job-1") -> Path:
    job_dir = tmp_path / "jobs" / name
    job_dir.mkdir(parents=True)
    write_model(job_dir / "job.json", JobSpec(job_id=name, kind="solve", project_dir=str(case.parent.parent), case_dir=str(case),
                                              provider=provider.serialise(), steps=steps))
    return job_dir


def _events(job_dir: Path) -> list[dict]:
    return [json.loads(ln) for ln in (job_dir / "events.jsonl").read_text().splitlines()]


def test_edits_between_steps_reach_the_staged_copy(tmp_path, staged):
    case, provider, remote = staged
    steps = [RemoveStep(name="clear_stop_trigger", globs=["aerobenchStop"]),
             StageStep(name="stage_in", direction="in"),
             CopyStep(name="schemes", src="system/fvSchemes.firstOrder", dst="system/fvSchemes"),
             DictSetStep(name="end", file="system/controlDict", key_path="endTime", value=4),
             FoamStep(name="solve", app=["fakeSolver"], solver=True, stage="solve"),
             StageStep(name="stage_out", direction="out")]
    job_dir = _job(tmp_path, case, provider, steps)
    assert Runner(job_dir).run() == 0
    st = read_state(job_dir)
    assert st.status == JobStatus.COMPLETED, st.message
    # the solver (running on the copy) saw both host-side edits
    assert (remote / "seen.txt").read_text() == "4 first-order"
    assert (case / "seen.txt").read_text() == "4 first-order"
    assert (case / "4" / "U").read_text() == "t=4"
    assert not (case / "5").exists()
    assert not any(e["kind"].startswith("staged_salvage") for e in _events(job_dir))


def test_graceful_stop_reaches_the_solver_and_results_come_back(tmp_path, staged):
    case, provider, remote = staged
    steps = [RemoveStep(name="clear_stop_trigger", globs=["aerobenchStop"]),
             StageStep(name="stage_in", direction="in"),
             FoamStep(name="solve", app=["fakeSolver"], solver=True, stage="solve"),
             StageStep(name="stage_out", direction="out")]
    provider.config["slow_copyback"] = 1.0
    job_dir = _job(tmp_path, case, provider, steps)
    seen_during_salvage: list[str] = []

    def request_stop():
        deadline = time.time() + 30
        while time.time() < deadline and not (remote / "3").exists():
            time.sleep(0.02)
        (job_dir / "control.json").write_text(json.dumps({"request": "stop"}))
        # while the copy-back runs, nothing may see a terminal status (the worker would
        # ingest a half-copied case and the UI would offer Resume)
        while time.time() < deadline:
            ev = (job_dir / "events.jsonl").read_text()
            if "staged_salvage_finished" in ev:
                break
            if "staged_salvage_started" in ev:
                seen_during_salvage.append(read_state(job_dir).status.value)
            time.sleep(0.05)

    t = threading.Thread(target=request_stop)
    t.start()
    assert Runner(job_dir).run() == 0
    t.join()
    assert seen_during_salvage and set(seen_during_salvage) == {"stopping"}
    st = read_state(job_dir)
    assert st.status == JobStatus.STOPPED
    kinds = [e["kind"] for e in _events(job_dir)]
    assert "graceful_stop_requested" in kinds and "graceful_stop_unavailable" not in kinds
    # the solver stopped through the trigger written into the copy (not a kill, not endTime)
    written = sorted(int(p.name) for p in remote.iterdir() if p.name.isdigit() and p.name != "0")
    assert 3 <= written[-1] < 1000
    log = (job_dir / st.steps[2].log).read_text()
    assert f"stopping at {written[-1]}" in log
    # ... and the latest written time was copied back to the host so the run can resume
    assert "staged_salvage_finished" in kinds
    assert (case / str(written[-1]) / "U").exists()


def test_failure_salvages_partial_results(tmp_path, staged):
    case, provider, remote = staged
    steps = [RemoveStep(name="clear_stop_trigger", globs=["aerobenchStop"]),
             StageStep(name="stage_in", direction="in"),
             DictSetStep(name="end", file="system/controlDict", key_path="endTime", value=10),
             FoamStep(name="solve", app=["fakeSolver", "3"], solver=True, stage="solve"),
             StageStep(name="stage_out", direction="out")]
    job_dir = _job(tmp_path, case, provider, steps)
    assert Runner(job_dir).run() == 1
    st = read_state(job_dir)
    assert st.status == JobStatus.FAILED
    assert st.steps[-1].status == "pending"  # the regular stage-out never ran ...
    assert (case / "3" / "U").read_text() == "t=3"  # ... but the salvage did
    assert any(e["kind"] == "staged_salvage_finished" and e["exit_code"] == 0 for e in _events(job_dir))


def test_staged_provider_without_mirroring_fails_instead_of_running_stale_input(tmp_path, staged):
    case, provider, remote = staged

    class NoMirror(FakeStagedProvider):
        kind = "fake-staged-nomirror"

        def push_files(self, case_dir, relpaths):
            return None

    registry.KINDS["fake-staged-nomirror"] = NoMirror
    try:
        p2 = NoMirror("nm", provider.config)
        steps = [RemoveStep(name="clear_stop_trigger", globs=["aerobenchStop"]),
                 StageStep(name="stage_in", direction="in"),
                 DictSetStep(name="end", file="system/controlDict", key_path="endTime", value=3),
                 FoamStep(name="solve", app=["fakeSolver"], solver=True),
                 StageStep(name="stage_out", direction="out")]
        job_dir = _job(tmp_path, case, p2, steps)
        assert Runner(job_dir).run() == 1
        st = read_state(job_dir)
        assert st.steps[2].status == "failed"
        assert st.steps[2].diagnostics[0].code == "environment.staging_unsupported"
        assert not (remote / "seen.txt").exists()  # the solver never ran on stale input
    finally:
        registry.KINDS.pop("fake-staged-nomirror", None)


def test_remove_step_handles_glob_characters_in_the_case_path(tmp_path, staged):
    _, provider, _ = staged
    case = tmp_path / "proj [v2]" / "runs" / "run-9"
    (case / "system").mkdir(parents=True)
    (case / "aerobenchStop").write_text("x")
    (case / "processor0").mkdir()
    steps = [RemoveStep(name="clean", globs=["aerobenchStop", "processor*"])]
    job_dir = _job(tmp_path, case, provider, steps, name="job-glob")
    assert Runner(job_dir).run() == 0
    assert not (case / "aerobenchStop").exists() and not (case / "processor0").exists()


def test_stage_in_refuses_to_overwrite_newer_staged_results(tmp_path):
    import subprocess

    from aerobench.execution.wsl import newer_results_guard

    host, staged_dir = tmp_path / "host case", tmp_path / "staged"
    for d in (host / "0", host / "100", staged_dir / "0", staged_dir / "processor0" / "250"):
        d.mkdir(parents=True)
    frag = newer_results_guard(shlex.quote(str(host)), shlex.quote(str(staged_dir)))
    r = subprocess.run(["bash", "-c", frag + " && echo OK"], capture_output=True, text=True)
    assert r.returncode == 3 and "250" in r.stderr and "100" in r.stderr
    (host / "250").mkdir()
    r = subprocess.run(["bash", "-c", frag + " && echo OK"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "OK"
    # nothing staged yet, or no time directories at all: fine
    r = subprocess.run(["bash", "-c", newer_results_guard(shlex.quote(str(host)), shlex.quote(str(tmp_path / "none"))) + " && echo OK"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "OK"


def test_failed_stage_in_is_not_copied_back(tmp_path, staged):
    case, provider, remote = staged
    provider.config["helper"] = str(tmp_path / "missing-helper.py")  # every staging command fails
    steps = [StageStep(name="stage_in", direction="in"), FoamStep(name="solve", app=["fakeSolver"], solver=True),
             StageStep(name="stage_out", direction="out")]
    job_dir = _job(tmp_path, case, provider, steps)
    assert Runner(job_dir).run() == 1
    assert not any(e["kind"].startswith("staged_salvage") for e in _events(job_dir))


def test_resumed_job_knows_the_copy_is_live(tmp_path, staged):
    """A job resumed after its stage-in completed must still mirror edits and salvage."""
    case, provider, remote = staged
    steps = [StageStep(name="stage_in", direction="in"),
             DictSetStep(name="end", file="system/controlDict", key_path="endTime", value=2),
             FoamStep(name="solve", app=["fakeSolver"], solver=True),
             StageStep(name="stage_out", direction="out")]
    job_dir = _job(tmp_path, case, provider, steps)
    # first attempt: only the stage-in
    r = Runner(job_dir)
    r._run_step(0, steps[0])
    r.save()
    (remote / "aerobenchStop").unlink()
    spec = json.loads((job_dir / "job.json").read_text())
    spec["resume_from_step"] = 1
    (job_dir / "job.json").write_text(json.dumps(spec))
    assert Runner(job_dir).run() == 0
    assert (remote / "seen.txt").read_text().startswith("2 ")
    assert (case / "2" / "U").exists()


def test_wsl_commands_quote_paths_and_reject_escapes(tmp_path):
    p = WSLProvider("wsl", {"distro": "Ubuntu"})
    case = Path("E:/OpenFOAM GUI/My car's project/runs/run-7")
    key = staged_case_key(case)
    assert key.startswith("runs/run-7-") and " " not in key and "'" not in key
    for spec in (p.stage_in(case), p.stage_out(case), p.push_files(case, ["system/fvSchemes"]),
                 p.remove_paths(case, ["processor*", "aerobenchStop"]), p.write_file(case, "aerobenchStop", "action=writeNow\n"),
                 p.command(["simpleFoam"], case, 4)):
        # --exec: WSL passes the arguments to bash verbatim; plain "--" would hand the raw command
        # line to the default shell, which expands $... and backticks before bash runs
        assert spec.argv[:6] == ["wsl.exe", "-d", "Ubuntu", "--exec", "bash", "-c"] and len(spec.argv) == 7
        script = spec.argv[-1]
        shlex.split(script)  # well-formed for the shell
        import subprocess

        assert subprocess.run(["bash", "-n", "-c", script]).returncode == 0  # valid bash syntax
        assert '"$HOME"/' in script
        assert "$PWD" not in script
    assert "'/mnt/e/OpenFOAM GUI/My car'\"'\"'s project/runs/run-7'" in p.stage_in(case).argv[-1]
    assert "pipefail" in p.stage_out(case).argv[-1]
    assert "-case \"$HOME\"/" in p.command(["simpleFoam"], case, 4).argv[-1]
    assert "exit 3" in p.stage_in(case).argv[-1]  # newer staged results are never overwritten
    with pytest.raises(ValueError):
        p.push_files(case, ["../../etc/passwd"])
    with pytest.raises(ValueError):
        p.remove_paths(case, ["*; rm -rf ~"])
    with pytest.raises(ValueError):
        p.write_file(case, "/tmp/x", "")
    # distinct host cases never share a staged directory
    assert staged_case_key(Path("E:/a/runs/run-1")) != staged_case_key(Path("E:/b/runs/run-1"))


def test_resume_steps_decompose_after_stage_in():
    from aerobench.domain.project import ProjectConfig
    from aerobench.foam.adapters import get_adapter
    from aerobench.services.runs import solve_steps

    cfg = ProjectConfig()
    adapter = get_adapter("opencfd", "v2606")
    names = [s.name for s in solve_steps(cfg, adapter, Path("."), 4, True, 0, resume=True, decompose_latest=True)]
    assert names.index("stage_in") < names.index("decomposePar") < names.index("solve") < names.index("stage_out")
