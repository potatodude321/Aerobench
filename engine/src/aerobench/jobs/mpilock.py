"""One parallel (MPI) job at a time on this computer, where the MPI cannot share it.

Microsoft MPI runs a local job through a process manager (smpd) that ``mpiexec`` starts for
it; two parallel jobs started side by side on the same computer interfere (the second one
fails, or ends when the first one does). Optimisations run several designs at once, so on
Windows every parallel OpenFOAM step takes this lock first and holds it while it runs; serial
steps (one process, no MPI) run side by side as before.

The lock is a named mutex (Windows) or ``flock`` on a file in the temporary folder (elsewhere,
used by tests and by providers that ask for it). Either is released by the operating system
when the holding process dies, so a crashed runner never blocks the others.
"""

from __future__ import annotations

import os
import tempfile
NAME = "AeroBench-MPI"


class MpiLock:
    def __init__(self, name: str = NAME):
        self.name = name
        self._handle = None
        self._fh = None
        self.held = False

    def acquire(self, timeout: float = 0.0) -> bool:
        """Try for up to ``timeout`` seconds; True once this process holds the lock."""
        if self.held:
            return True
        if os.name == "nt":
            self.held = self._acquire_nt(timeout)
        else:
            self.held = self._acquire_posix(timeout)
        return self.held

    def release(self) -> None:
        if not self.held:
            return
        try:
            if os.name == "nt":
                import ctypes

                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.ReleaseMutex(self._handle)
            else:
                import fcntl

                fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self.held = False

    def close(self) -> None:
        self.release()
        if os.name == "nt" and self._handle is not None:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
            self._handle = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------ platforms
    def _acquire_nt(self, timeout: float) -> bool:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if self._handle is None:
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateMutexW.restype = wintypes.HANDLE
            self._handle = k32.CreateMutexW(None, False, f"Local\\{self.name}")
            if not self._handle:
                raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        r = k32.WaitForSingleObject(self._handle, max(0, int(timeout * 1000)))
        # WAIT_OBJECT_0, or WAIT_ABANDONED: the previous holder died; the mutex is ours either way
        return r in (0x0, 0x80)

    def _acquire_posix(self, timeout: float) -> bool:
        import fcntl
        import time

        if self._fh is None:
            self._fh = open(os.path.join(tempfile.gettempdir(), f"{self.name}.lock"), "a+")
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if time.monotonic() >= end:
                    return False
                time.sleep(min(0.2, max(0.0, end - time.monotonic())))


def needs_lock(provider, nprocs: int) -> bool:
    return nprocs > 1 and bool(getattr(provider, "one_mpi_job_at_a_time", False))


def waiting_message() -> str:
    return ("Waiting for another parallel run on this computer to finish (Microsoft MPI runs one parallel job at a time; "
            "this one starts as soon as it is free).")


__all__ = ["MpiLock", "needs_lock", "waiting_message", "NAME"]
