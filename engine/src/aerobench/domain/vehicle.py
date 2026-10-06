"""Vehicle definition: wheels, axles, derived dimensions (CFD frame, metres)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
from pydantic import Field, field_validator

from .common import Model, Vec3, unit
from .geometry import WheelPosition


class WheelRotation(str, Enum):
    VEHICLE_SPEED = "vehicle_speed"  # omega = |U_ground| / R
    CUSTOM = "custom"
    STATIONARY = "stationary"


class WheelModel(str, Enum):
    ROTATING_WALL = "rotating_wall"  # rotatingWallVelocity approximation (default)
    MRF = "mrf"  # advanced, not yet generated — kept for schema stability
    AMI = "ami"  # advanced, not yet generated


class Wheel(Model):
    position: WheelPosition
    component_id: Optional[str] = Field(None, description="Geometry component providing the wheel surface")
    centre: Vec3
    axis: Vec3 = (0.0, -1.0, 0.0)
    radius: float = Field(gt=0, description="Rolling radius (m)")
    width: Optional[float] = Field(None, gt=0)
    rotation: WheelRotation = WheelRotation.VEHICLE_SPEED
    custom_omega: Optional[float] = Field(None, description="rad/s, used when rotation == custom")
    source: str = Field("manual", description="'auto' (detected) or 'manual'")
    detection_confidence: Optional[float] = None

    @field_validator("axis")
    @classmethod
    def _normalise_axis(cls, v):
        a = unit(v)
        # Canonical orientation: the axis whose positive rotation makes the contact patch
        # move rearward (+x) is -y (ADR-0008).  Accept either sign from the user, then fix.
        if a[1] > 0:
            a = -a
        return (float(a[0]), float(a[1]), float(a[2]))


class VehicleCategory(str, Enum):
    ROAD_CAR = "road_car"
    RACE_CAR = "race_car"
    FORMULA_STUDENT = "formula_student"
    BLUFF_BODY = "bluff_body"  # e.g. Ahmed body — no wheels


class VehicleConfig(Model):
    category: VehicleCategory = VehicleCategory.ROAD_CAR
    wheels: list[Wheel] = Field(default_factory=list)
    centreline_y: float = 0.0
    ride_height_offset: float = Field(
        0.0, description="Rigid vertical offset applied to the whole vehicle (m); +ve raises the car"
    )
    rake_deg: float = Field(0.0, description="Pitch rotation about the rear-axle ground contact; +ve = nose down")

    @field_validator("wheels")
    @classmethod
    def _unique_positions(cls, v):
        pos = [w.position for w in v]
        if len(pos) != len(set(pos)):
            raise ValueError("wheel positions must be unique")
        return v

    def wheel(self, pos: WheelPosition) -> Optional[Wheel]:
        for w in self.wheels:
            if w.position == pos:
                return w
        return None

    @property
    def front_axle_x(self) -> Optional[float]:
        xs = [w.centre[0] for w in self.wheels if w.position in (WheelPosition.FL, WheelPosition.FR)]
        return float(np.mean(xs)) if xs else None

    @property
    def rear_axle_x(self) -> Optional[float]:
        xs = [w.centre[0] for w in self.wheels if w.position in (WheelPosition.RL, WheelPosition.RR)]
        return float(np.mean(xs)) if xs else None

    @property
    def wheelbase(self) -> Optional[float]:
        f, r = self.front_axle_x, self.rear_axle_x
        if f is None or r is None:
            return None
        return float(r - f)  # nose at -x => rear axle has larger x

    def wheelbase_mid_ground(self, ground_z: float = 0.0) -> Optional[Vec3]:
        f, r = self.front_axle_x, self.rear_axle_x
        if f is None or r is None:
            return None
        return (0.5 * (f + r), self.centreline_y, ground_z)
