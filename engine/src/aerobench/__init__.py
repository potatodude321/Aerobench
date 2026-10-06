"""AeroBench (working title) — automotive external-aerodynamics workbench engine.

The engine is UI-agnostic: everything the desktop application does is available
through :mod:`aerobench.api` (HTTP service), :mod:`aerobench.cli` and the Python API.
"""

from .version import APP_VERSION, APP_DISPLAY_NAME

__all__ = ["APP_VERSION", "APP_DISPLAY_NAME"]
