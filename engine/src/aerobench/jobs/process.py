"""Cross-platform process helpers: detached launch, liveness, tree termination."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

import psutil

WINDOWS = os.name == "nt"
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def popen_kwargs(detached: bool) -> dict:
    if WINDOWS:
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        if detached:
            flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    return {"start_new_session": True}


def launch_detached(argv: list[str], cwd: str, log_path: str, env: Optional[dict] = None) -> subprocess.Popen:
    """Start a process that outlives its parent (engine/GUI)."""
    log = open(log_path, "ab")
    kw = popen_kwargs(detached=True)
    try:
        return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, close_fds=True, **kw)
    except OSError:
        if WINDOWS:  # breakaway may be refused inside some job objects
            kw["creationflags"] &= ~CREATE_BREAKAWAY_FROM_JOB
            return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, close_fds=True, **kw)
        raise
    finally:
        log.close()


def create_time(pid: int) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def is_alive(pid: Optional[int], created: Optional[float]) -> bool:
    """True if ``pid`` exists *and* is the same process (create time match guards PID reuse)."""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            return False
        if created is not None and abs(p.create_time() - created) > 2.0:
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def kill_tree(pid: int, timeout: float = 10.0, graceful: bool = True) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    if graceful:
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(procs, timeout=timeout)
    else:
        alive = procs
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)


def python_executable() -> str:
    exe = sys.executable
    if WINDOWS and exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.exists(w):
            return w  # no console window for detached runners
    return exe
