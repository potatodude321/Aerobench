"""Observables and run-time function-object configuration."""

from __future__ import annotations

from pydantic import Field

from .common import Model, Vec3


class Probe(Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    points: list[Vec3]
    fields: list[str] = Field(default_factory=lambda: ["p", "U"])


class ObservablesConfig(Model):
    component_forces: bool = True
    yplus: bool = True
    wall_shear_stress: bool = True
    pressure_coefficient: bool = True
    vorticity: bool = False
    q_criterion: bool = False
    continuity: bool = True
    probes: list[Probe] = Field(default_factory=list)
    force_write_interval: int = 1
