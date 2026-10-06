"""Mesh configuration: base cell, surface refinement, refinement volumes, layers, quality."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import Field

from .common import Model, Vec3
from .geometry import ComponentRole


class MeshPreset(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH_ACCURACY = "high_accuracy"
    CUSTOM = "custom"


MESH_PRESET_VERSION = "1"


class BoxShape(Model):
    kind: Literal["box"] = "box"
    min: Vec3
    max: Vec3


class RotatedBoxShape(Model):
    """OpenFOAM ``rotatedBox``: corner ``origin``, extents ``span`` along local e1, e2, e3."""

    kind: Literal["rotated_box"] = "rotated_box"
    origin: Vec3
    span: Vec3
    e1: Vec3
    e3: Vec3 = (0.0, 0.0, 1.0)


class CylinderShape(Model):
    kind: Literal["cylinder"] = "cylinder"
    point1: Vec3
    point2: Vec3
    radius: float = Field(gt=0)


class SphereShape(Model):
    kind: Literal["sphere"] = "sphere"
    centre: Vec3
    radius: float = Field(gt=0)


Shape = Union[BoxShape, RotatedBoxShape, CylinderShape, SphereShape]


class RegionKind(str, Enum):
    VEHICLE_BOX = "vehicle_box"
    WAKE_NEAR = "wake_near"
    WAKE_FAR = "wake_far"
    UNDERBODY = "underbody"
    WHEEL = "wheel"
    FRONT = "front"
    DIFFUSER = "diffuser"
    USER = "user"


class RefinementRegion(Model):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str
    kind: RegionKind = RegionKind.USER
    shape: Shape = Field(discriminator="kind")
    level: int = Field(ge=0, le=12, description="Refinement level relative to the base cell")
    enabled: bool = True
    auto: bool = Field(True, description="Managed by the automotive preset; regenerated when the vehicle changes")


class SurfaceRefinement(Model):
    """Surface refinement per component role (or explicit component ids)."""

    role: Optional[ComponentRole] = None
    component_ids: list[str] = Field(default_factory=list)
    min_level: int = Field(ge=0, le=12)
    max_level: int = Field(ge=0, le=12)
    feature_level: Optional[int] = Field(None, ge=0, le=12, description="Edge-feature refinement level")
    layers: bool = True


class LayerMode(str, Enum):
    TARGET_YPLUS = "target_yplus"
    RELATIVE = "relative"
    NONE = "none"


class LayerConfig(Model):
    mode: LayerMode = LayerMode.TARGET_YPLUS
    target_yplus: float = Field(50.0, gt=0)
    n_layers: Optional[int] = Field(None, ge=1, le=30, description="None = designed automatically")
    expansion_ratio: float = Field(1.2, ge=1.0, le=2.0)
    final_relative: float = Field(0.5, gt=0, le=1.0, description="Final layer / surface cell (relative mode or target)")
    min_thickness_relative: float = Field(0.1, gt=0, le=1.0)
    ground_layers: bool = False
    correlation: Literal["schlichting_1979", "power_law_1_7"] = "schlichting_1979"
    feature_angle: float = 130.0
    n_layer_iter: int = 50


class QualityControls(Model):
    max_non_ortho: float = 65.0
    max_boundary_skewness: float = 20.0
    max_internal_skewness: float = 4.0
    max_concave: float = 80.0
    min_vol: float = 1e-13
    min_tet_quality: float = 1e-15
    min_twist: float = 0.02
    min_determinant: float = 0.001
    min_face_weight: float = 0.02
    min_vol_ratio: float = 0.01
    n_smooth_scale: int = 4
    error_reduction: float = 0.75
    relaxed_max_non_ortho: float = 75.0


class MeshConfig(Model):
    preset: MeshPreset = MeshPreset.BALANCED
    preset_version: str = MESH_PRESET_VERSION
    base_cell_size: Optional[float] = Field(None, gt=0, description="Background (level-0) cell edge (m); None = from preset")
    surface: list[SurfaceRefinement] = Field(default_factory=list)
    regions: list[RefinementRegion] = Field(default_factory=list)
    auto_regions: bool = Field(True, description="Generate automotive refinement volumes from vehicle dimensions")
    layers: LayerConfig = Field(default_factory=LayerConfig)
    quality: QualityControls = Field(default_factory=QualityControls)
    n_cells_between_levels: int = Field(3, ge=1, le=8)
    resolve_feature_angle: float = 30.0
    feature_included_angle: float = 150.0
    max_global_cells: int = 50_000_000
    max_local_cells: int = 5_000_000
    snap_tolerance: float = 2.0
    n_snap_solve_iter: int = 50
    n_feature_snap_iter: int = 10
    parallel: bool = True
