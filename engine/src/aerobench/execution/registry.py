"""Provider registry, defaults and hardware information."""

from __future__ import annotations

import os
import platform
from typing import Any, Optional

import psutil

from .local_posix import LocalPosixProvider
from .native_windows import NativeWindowsProvider
from .provider import ExecutionProvider, is_windows
from .remote import RemoteSSHProvider

KINDS = {
    "native-windows": NativeWindowsProvider,
    "local-posix": LocalPosixProvider,
    "remote-ssh": RemoteSSHProvider,
}


def provider_from_dict(d: dict[str, Any]) -> ExecutionProvider:
    cls = KINDS[d["kind"]]
    return cls(d.get("id", d["kind"]), d.get("config"))


# WSL was removed in 0.1.0 build 3: the bundled native Windows OpenFOAM is the supported path.
# Settings written by earlier builds may still name it; such entries are ignored.
RETIRED_KINDS = {"wsl"}


def default_providers(settings: Optional[dict[str, Any]] = None) -> list[ExecutionProvider]:
    """The providers this machine can offer, default first."""
    settings = settings or {}
    configured = [provider_from_dict(p) for p in settings.get("providers", []) if p.get("kind") in KINDS]
    if configured:
        return configured
    if is_windows():
        return [NativeWindowsProvider("native", settings.get("native"))]
    return [LocalPosixProvider("local", settings.get("local"))]


def choose_provider(providers: list[ExecutionProvider], provider_id: Optional[str] = None) -> Optional[ExecutionProvider]:
    if provider_id:
        for p in providers:
            if p.id == provider_id:
                return p
        return None
    for p in providers:
        if p.info().available:
            return p
    return providers[0] if providers else None


def hardware_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "memory_gb": round(vm.total / 2**30, 1),
        "python": platform.python_version(),
        "hostname_hash": __import__("hashlib").sha256(platform.node().encode()).hexdigest()[:12],
    }


def usable_cores() -> int:
    """Physical cores (OpenFOAM scales poorly on hyper-threads), at least 1."""
    return max(1, psutil.cpu_count(logical=False) or os.cpu_count() or 1)
