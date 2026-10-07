"""What of AeroBench is running from an installation folder — used by the setup before it
replaces or removes files (run with the installation's embedded Python).

    python -I running.py check      <install-dir>   exit 0 nothing, 10 app only, 20 simulations
    python -I running.py close-app  <install-dir>   end the application, engine, worker, ParaView
    python -I running.py stop-sims  <install-dir>   graceful stop of every running job, wait

The bundled OpenFOAM lives inside the installation folder, so a running simulation would
otherwise be killed by the setup without warning. Simulations are stopped the way the app
stops them: a stop request in the job folder makes the runner write the current state and
exit; the run can be resumed after the update. The worker is closed first so it does not
start queued jobs meanwhile (they stay queued and start when the app runs again).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import psutil

GRACE = 660  # the runner waits up to 600 s for a solver to write and exit


def _under(path: str, root: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(root)) + os.sep)
    except (TypeError, ValueError):
        return False


def scan(install: str) -> tuple[list[psutil.Process], list[tuple[psutil.Process, Path | None]]]:
    """(application processes, [(runner process or solver, job dir)])."""
    app, sims = [], []
    # never this process or the setup/uninstaller that started it (an uninstaller run by an
    # update executes from inside the installation folder)
    me = psutil.Process()
    skip = {me.pid} | {a.pid for a in me.parents()}
    for p in psutil.process_iter(["pid", "exe", "cmdline", "status"]):
        if p.info["pid"] in skip or p.info.get("status") == psutil.STATUS_ZOMBIE:
            continue
        exe = p.info.get("exe") or ""
        cmd = p.info.get("cmdline") or []
        if not exe or not _under(exe, install):
            continue
        if "aerobench.jobs.runner" in cmd:
            i = cmd.index("aerobench.jobs.runner")
            job = Path(cmd[i + 1]) if i + 1 < len(cmd) else None
            sims.append((p, job))
        elif _under(exe, os.path.join(install, "resources", "openfoam")):
            sims.append((p, None))  # a solver; its runner (if any) is listed separately
        else:
            app.append(p)
    return app, sims


def close_app(install: str) -> int:
    app, _ = scan(install)
    for p in app:
        try:
            p.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(app, timeout=10)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return 0


def stop_sims(install: str) -> int:
    _, sims = scan(install)
    for _, job in sims:
        if job is not None and job.is_dir():
            tmp = job / "control.json.tmp"
            tmp.write_text(json.dumps({"request": "stop", "by": "setup"}), encoding="utf-8")
            os.replace(tmp, job / "control.json")
    deadline = time.time() + GRACE
    while time.time() < deadline:
        _, sims = scan(install)
        if not any(job is not None for _, job in sims):
            break  # every runner has finished (state written)
        time.sleep(2)
    else:
        return 1
    # solvers left without a runner (orphans of a crashed runner: their job is already marked
    # interrupted) are ended
    _, sims = scan(install)
    procs = [p for p, _ in sims]
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=15)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return 0 if not scan(install)[1] else 1


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("check", "close-app", "stop-sims"):
        print(__doc__, file=sys.stderr)
        return 64
    mode, install = argv
    if mode == "check":
        app, sims = scan(install)
        print(f"application processes: {len(app)}, simulations: {len(sims)}")
        return 20 if sims else (10 if app else 0)
    if mode == "close-app":
        return close_app(install)
    return stop_sims(install)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
