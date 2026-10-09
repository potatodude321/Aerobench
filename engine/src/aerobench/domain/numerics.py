"""Numerics configuration.  Presets map to real dictionaries in the fork adapter.

The preset *version* is persisted so that a later software release can change the
preset definition without silently changing old projects (BUILD_SPEC §10).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import Field

from .common import Model


class NumericsPreset(str, Enum):
    ROBUST = "robust"
    BALANCED = "balanced"
    FAST_CONVERGENCE = "fast_convergence"
    HIGH_FIDELITY = "high_fidelity"
    CUSTOM = "custom"


NUMERICS_PRESET_VERSION = "1"


class NumericsConfig(Model):
    preset: NumericsPreset = NumericsPreset.BALANCED
    preset_version: str = NUMERICS_PRESET_VERSION
    base_preset: NumericsPreset = Field(
        NumericsPreset.BALANCED, description="For CUSTOM: the preset the custom values started from"
    )
    iterations: int = Field(1500, ge=10, le=200000)
    first_order_iterations: Optional[int] = Field(
        None, ge=0, description="Run this many iterations with first-order convection before switching (None = preset)"
    )
    potential_init: bool = True
    write_interval: Optional[int] = Field(None, ge=1, description="None = write at end and every 500 iterations")
    averaging_fraction: float = Field(0.2, gt=0, le=0.8, description="Final fraction of iterations used for averaged coefficients")
    stop_when_converged: bool = Field(
        False, description="Stop the solver (state written) as soon as every convergence criterion is met; "
        "the remaining iterations are skipped")
    overrides: dict[str, Any] = Field(default_factory=dict, description="Custom numeric overrides (relaxation, tolerances)")
