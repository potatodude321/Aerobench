"""Geometry configuration: revisions, transform into the CFD frame, components."""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
from pydantic import Field, field_validator, model_validator

from .common import AxisDir, Model, Vec3


class GeometryTransform(Model):
    """Maps raw source coordinates into the CFD frame (ADR-0007).

    ``p_cfd = R @ (unit_scale * p_src) + translation`` where ``R`` maps the source
    file's *forward* direction to ``-x`` and its *up* direction to ``+z``.
    """

    unit_scale: float = Field(1.0, gt=0, description="Multiply raw coordinates to obtain metres")
    forward_axis: AxisDir = Field(AxisDir.NX, description="Direction the vehicle nose points in the source file")
    up_axis: AxisDir = Field(AxisDir.PZ, description="Up direction in the source file")
    translation: Vec3 = (0.0, 0.0, 0.0)
    auto_place: bool = Field(
        True,
        description="Recompute translation so the vehicle is centred laterally, its lowest point "
        "sits on z=0 and its nose at x=0 (before ride-height and contact-patch treatment).",
    )

    @model_validator(mode="after")
    def _axes_independent(self):
        if self.forward_axis.value[1] == self.up_axis.value[1]:
            raise ValueError("forward_axis and up_axis must be different axes")
        return self

    def rotation(self) -> np.ndarray:
        f = self.forward_axis.vector()
        u = self.up_axis.vector()
        # Source basis expressed in CFD coordinates: forward -> -x, up -> +z,
        # right = forward x up (in source) -> +y.
        r_src = np.cross(f, u)
        src = np.column_stack([f, r_src, u])  # columns: source vectors
        dst = np.column_stack([[-1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
        # R @ src = dst  => R = dst @ src^T (src is orthonormal)
        return dst @ src.T

    def matrix4(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3] = self.rotation() * self.unit_scale
        m[:3, 3] = np.asarray(self.translation)
        return m

    def apply(self, pts: np.ndarray) -> np.ndarray:
        return (np.asarray(pts, dtype=float) * self.unit_scale) @ self.rotation().T + np.asarray(self.translation)


class ComponentRole(str, Enum):
    BODY = "body"
    WHEEL = "wheel"
    WING = "wing"
    SPOILER = "spoiler"
    SPLITTER = "splitter"
    DIFFUSER = "diffuser"
    FLOOR = "floor"
    MIRROR = "mirror"
    COOLING_INLET = "cooling_inlet"
    COOLING_OUTLET = "cooling_outlet"
    STILT = "stilt"
    OTHER = "other"


class WheelPosition(str, Enum):
    FL = "FL"
    FR = "FR"
    RL = "RL"
    RR = "RR"


class GeometryComponent(Model):
    """A named, force-reportable part of the vehicle.  Becomes an OpenFOAM patch."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="Patch/surface name in the generated case")
    name: str
    source_regions: list[str] = Field(default_factory=list, description="Region (solid/group) names in the geometry revision")
    role: ComponentRole = ComponentRole.BODY
    wheel_position: Optional[WheelPosition] = None
    include_in_forces: bool = True
    suggested: bool = Field(False, description="Created by automatic identification (user may override)")

    @model_validator(mode="after")
    def _wheel_position(self):
        if self.wheel_position is not None and self.role != ComponentRole.WHEEL:
            raise ValueError("wheel_position is only valid for role 'wheel'")
        return self


class GeometrySummary(Model):
    """Snapshot of measured properties in the CFD frame (metres)."""

    bbox_min: Vec3
    bbox_max: Vec3
    triangle_count: int
    surface_area: float
    region_names: list[str] = Field(default_factory=list)

    @property
    def length(self) -> float:
        return self.bbox_max[0] - self.bbox_min[0]

    @property
    def width(self) -> float:
        return self.bbox_max[1] - self.bbox_min[1]

    @property
    def height(self) -> float:
        return self.bbox_max[2] - self.bbox_min[2]


class GeometryConfig(Model):
    revision_id: Optional[str] = Field(None, description="Active geometry revision (content-addressed in the project DB)")
    transform: GeometryTransform = Field(default_factory=GeometryTransform)
    components: list[GeometryComponent] = Field(default_factory=list)
    summary: Optional[GeometrySummary] = Field(None, description="Measured after transform; refreshed on change")

    @field_validator("components")
    @classmethod
    def _unique_ids(cls, v: list[GeometryComponent]):
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("component ids must be unique")
        regions = [r for c in v for r in c.source_regions]
        if len(regions) != len(set(regions)):
            raise ValueError("a source region may belong to only one component")
        return v

    def component(self, cid: str) -> GeometryComponent:
        for c in self.components:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def by_role(self, role: ComponentRole) -> list[GeometryComponent]:
        return [c for c in self.components if c.role == role]
