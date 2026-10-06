"""Application context: settings, execution providers, ParaView detection (shared by API, CLI, worker)."""

from __future__ import annotations

import threading
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..execution.provider import ExecutionProvider, ProviderInfo
from ..execution.registry import choose_provider, default_providers, hardware_info, usable_cores
from ..settings import AppSettings


class AppContext:
    def __init__(self, settings: Optional[AppSettings] = None):
        self.settings = settings or AppSettings.load()
        self._providers: Optional[list[ExecutionProvider]] = None
        self._lock = threading.Lock()
        self._paraview: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------------ providers
    def providers(self, refresh: bool = False) -> list[ExecutionProvider]:
        with self._lock:
            if self._providers is None or refresh:
                self._providers = default_providers(self.settings.model_dump())
                for p in self._providers:
                    p.info(refresh=True)
            return self._providers

    def provider(self, provider_id: Optional[str] = None) -> ExecutionProvider:
        p = choose_provider(self.providers(), provider_id or self.settings.default_provider)
        if p is None:
            raise AeroBenchError.make("environment.no_provider", ErrorCategory.ENVIRONMENT, "No OpenFOAM execution provider is configured.",
                                      action="Open Settings → Solver and install or locate OpenFOAM v2606.")
        return p

    def require_provider(self, provider_id: Optional[str] = None) -> tuple[ExecutionProvider, ProviderInfo]:
        p = self.provider(provider_id)
        info = p.info()
        if not info.available or info.blocking_problems:
            d = info.blocking_problems[0] if info.blocking_problems else None
            raise AeroBenchError(d) if d else AeroBenchError.make(
                "environment.provider_unavailable", ErrorCategory.ENVIRONMENT, f"The execution provider '{p.id}' is not available.")
        return p, info

    def max_cores(self) -> int:
        return max(1, min(self.settings.max_cores or usable_cores(), usable_cores() * 2))

    def hardware(self) -> dict[str, Any]:
        return hardware_info()

    # ------------------------------------------------------------------ ParaView
    def paraview(self, refresh: bool = False) -> dict[str, Any]:
        if self._paraview is None or refresh:
            from ..viz.detect import detect_paraview

            self._paraview = detect_paraview(self.settings.paraview.pvpython)
        return self._paraview

    def environment_report(self) -> dict[str, Any]:
        return {
            "providers": [p.info().as_dict() for p in self.providers()],
            "default_provider": (self.provider().id if self.providers() else None),
            "paraview": self.paraview(),
            "hardware": self.hardware(),
            "max_cores": self.max_cores(),
        }
