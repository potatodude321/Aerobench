"""Engine-side client for the pvpython sidecar (process lifecycle, requests, caching)."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import OrderedDict
from importlib import resources
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory

PREFIX = "@@AB@@"


def server_script() -> str:
    return str(resources.files("aerobench.viz").joinpath("server/pv_server.py"))


class SidecarError(RuntimeError):
    pass


class ParaViewSidecar:
    def __init__(self, pvpython: str, startup_timeout: float = 120.0):
        self.pvpython = pvpython
        self.proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.Lock()
        self._id = 0
        self.stderr_tail: list[str] = []
        self.startup_timeout = startup_timeout
        self.case: Optional[str] = None
        self._open_params: Optional[dict] = None

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        argv = [self.pvpython, "--force-offscreen-rendering", server_script()]
        kw: dict[str, Any] = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     bufsize=1, encoding="utf-8", errors="replace", **kw)
        self._q = queue.Queue()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        ready = self._wait(0, self.startup_timeout)
        if not ready.get("ok"):
            raise SidecarError("ParaView sidecar failed to start")
        self.case = None

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            if line.startswith(PREFIX):
                try:
                    self._q.put(json.loads(line[len(PREFIX):]))
                except json.JSONDecodeError:
                    pass
        self._q.put({"id": -1, "ok": False, "error": "sidecar exited"})

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip())
            del self.stderr_tail[:-200]

    def _wait(self, rid: int, timeout: float) -> dict:
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise SidecarError(f"ParaView request {rid} timed out")
            try:
                msg = self._q.get(timeout=left)
            except queue.Empty:
                continue
            if msg.get("id") == rid:
                return msg
            if msg.get("id") == -1:
                raise SidecarError("ParaView sidecar exited unexpectedly:\n" + "\n".join(self.stderr_tail[-30:]))

    def _send(self, op: str, params: dict, timeout: float) -> dict:
        self._id += 1
        rid = self._id
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps({"id": rid, "op": op, "params": params}) + "\n")
        self.proc.stdin.flush()
        return self._wait(rid, timeout)

    def request(self, op: str, timeout: float = 600.0, **params: Any) -> dict:
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self.start()
                # a restarted sidecar (crash, or closed by the pool meanwhile) has no case open:
                # reopen the one its users believe is open before running their request
                if op != "open_case" and self._open_params is not None:
                    reopened = self._send("open_case", self._open_params, timeout)
                    if reopened.get("ok"):
                        self.case = self._open_params.get("foam_file")
            msg = self._send(op, params, timeout)
            if op == "open_case" and msg.get("ok"):
                self._open_params = dict(params)
        if not msg.get("ok"):
            raise AeroBenchError.make("visualisation.request_failed", ErrorCategory.VISUALISATION,
                                      f"ParaView could not complete '{op}': {msg.get('error')}",
                                      details=msg.get("traceback", ""))
        return msg["result"]

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                with self._lock:
                    assert self.proc.stdin
                    self.proc.stdin.write(json.dumps({"id": 999999, "op": "shutdown"}) + "\n")
                    self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        self.proc = None


class SidecarPool:
    """At most ``size`` live sidecars, one per opened case (LRU)."""

    def __init__(self, pvpython: str, size: int = 2):
        self.pvpython = pvpython
        self.size = size
        self._items: "OrderedDict[str, ParaViewSidecar]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, case_key: str) -> ParaViewSidecar:
        with self._lock:
            sc = self._items.pop(case_key, None)
            if sc is None:
                sc = ParaViewSidecar(self.pvpython)
                if len(self._items) >= self.size:
                    _, old = self._items.popitem(last=False)
                    old.close()
            self._items[case_key] = sc
            return sc

    def close_all(self) -> None:
        with self._lock:
            for sc in self._items.values():
                sc.close()
            self._items.clear()
