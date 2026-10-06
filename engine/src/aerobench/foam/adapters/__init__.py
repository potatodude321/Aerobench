from __future__ import annotations

from .base import ForkAdapter
from .foundation import FoundationAdapter
from .opencfd import OpenCFDAdapter


def get_adapter(fork: str, version: str, allow_experimental: bool = False) -> ForkAdapter:
    from ...core.errors import AeroBenchError, ErrorCategory

    if fork == "opencfd":
        return OpenCFDAdapter(version)
    if fork == "foundation":
        if not allow_experimental:
            raise AeroBenchError.make(
                "solver.adapter_experimental",
                ErrorCategory.DEPENDENCY,
                f"The OpenFOAM Foundation v{version} adapter is experimental and not validated.",
                action="Use OpenCFD OpenFOAM v2606, or enable experimental adapters in Settings.",
            )
        return FoundationAdapter(version)
    raise AeroBenchError.make("solver.unknown_fork", ErrorCategory.DEPENDENCY, f"Unknown OpenFOAM fork '{fork}'.")


__all__ = ["ForkAdapter", "OpenCFDAdapter", "FoundationAdapter", "get_adapter"]
