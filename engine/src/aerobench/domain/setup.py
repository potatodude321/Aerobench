"""Operating condition, reference values, tunnel and physics configuration."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field

from .common import Model, Vec3


# --------------------------------------------------------------------------- operating


class GroundMode(str, Enum):
    MOVING = "moving"
    STATIONARY = "stationary"


class YawGroundMode(str, Enum):
    ROAD = "road"  # ground moves at U cos(beta) along vehicle axis (crosswind on road)
    TUNNEL_BELT = "tunnel_belt"  # belt along freestream at U (turntable tunnel)


class OperatingCondition(Model):
    speed: float = Field(40.0, gt=0, le=150, description="Resultant freestream speed U∞ (m/s)")
    yaw_deg: float = Field(0.0, ge=-30, le=30, description="+ve: relative wind from the vehicle's right (ADR-0007)")
    ground: GroundMode = GroundMode.MOVING
    yaw_ground_mode: YawGroundMode = YawGroundMode.ROAD
    wheels_rotate: bool = True
    pressure: float = Field(0.0, description="Kinematic reference pressure (p/rho, m²/s²) at the far field")


# --------------------------------------------------------------------------- reference


class ForceAxes(str, Enum):
    BODY = "body"
    WIND = "wind"


class AreaSource(str, Enum):
    COMPUTED_FRONTAL = "computed_frontal"
    MANUAL = "manual"


class MomentCentreSource(str, Enum):
    WHEELBASE_MID_GROUND = "wheelbase_mid_ground"
    VEHICLE_CENTRE_GROUND = "vehicle_centre_ground"
    MANUAL = "manual"


class ReferenceValues(Model):
    area: Optional[float] = Field(None, gt=0, description="Reference area (m²)")
    area_source: AreaSource = AreaSource.COMPUTED_FRONTAL
    area_computed: Optional[float] = Field(None, description="Last frontal-area computation (m²)")
    area_computed_resolution: Optional[float] = Field(None, description="Pixel size used (m)")
    length: Optional[float] = Field(None, gt=0, description="Reference length (m); default = wheelbase or vehicle length")
    length_source: str = "wheelbase"
    density: float = Field(1.204, gt=0, description="Reference density (kg/m³) used to dimensionalise kinematic pressure")
    velocity: Optional[float] = Field(None, gt=0, description="Reference velocity (m/s); None = operating speed")
    moment_centre: Optional[Vec3] = None
    moment_centre_source: MomentCentreSource = MomentCentreSource.WHEELBASE_MID_GROUND
    axes: ForceAxes = ForceAxes.BODY
    locked: bool = Field(False, description="Set once results exist; changing requires explicit confirmation")


# --------------------------------------------------------------------------- tunnel


class DomainPreset(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH_ACCURACY = "high_accuracy"
    CUSTOM = "custom"


DOMAIN_PRESETS: dict[DomainPreset, dict[str, float]] = {
    # multipliers of vehicle length L (upstream/downstream from nose/tail) and of L for the
    # lateral (from vehicle side) and vertical (from vehicle top) clearances.
    DomainPreset.FAST: dict(upstream=2.0, downstream=5.0, lateral=1.2, top=1.2),
    DomainPreset.BALANCED: dict(upstream=3.0, downstream=7.0, lateral=2.0, top=2.0),
    DomainPreset.HIGH_ACCURACY: dict(upstream=4.0, downstream=10.0, lateral=3.0, top=3.0),
}


class Symmetry(str, Enum):
    FULL = "full"
    HALF = "half"  # symmetryPlane at the vehicle centreline (y = centreline_y), keep y <= c


class FarfieldBC(str, Enum):
    AUTO = "auto"  # classic at zero yaw, freestream otherwise
    INLET_OUTLET_SLIP = "inlet_outlet_slip"
    FREESTREAM = "freestream"


class TunnelConfig(Model):
    preset: DomainPreset = DomainPreset.BALANCED
    upstream: float = Field(3.0, gt=0)
    downstream: float = Field(7.0, gt=0)
    lateral: float = Field(2.0, gt=0)
    top: float = Field(2.0, gt=0)
    symmetry: Symmetry = Symmetry.FULL
    contact_patch_fraction: float = Field(
        0.01, ge=0, le=0.05, description="Ground raised into tyres by this fraction of rolling radius (ADR-0008)"
    )
    farfield_bc: FarfieldBC = FarfieldBC.AUTO
    design_yaw_range_deg: Optional[tuple[float, float]] = Field(
        None, description="Size wake refinement/domain for this yaw range (enables mesh reuse in yaw sweeps)"
    )

    def apply_preset(self, preset: DomainPreset) -> "TunnelConfig":
        if preset == DomainPreset.CUSTOM:
            return self.model_copy(update={"preset": preset})
        return self.model_copy(update={"preset": preset, **DOMAIN_PRESETS[preset]})


# --------------------------------------------------------------------------- physics


class TurbulenceModel(str, Enum):
    K_OMEGA_SST = "kOmegaSST"
    SPALART_ALLMARAS = "SpalartAllmaras"
    REALIZABLE_KE = "realizableKE"
    GEKO = "GEKO"
    LAMINAR = "laminar"


class InletTurbulenceSpec(str, Enum):
    VISCOSITY_RATIO = "viscosity_ratio"
    LENGTH_SCALE = "length_scale"


class InletTurbulence(Model):
    intensity: float = Field(0.01, gt=0, le=0.2)
    spec: InletTurbulenceSpec = InletTurbulenceSpec.VISCOSITY_RATIO
    viscosity_ratio: float = Field(10.0, gt=0)
    length_scale: Optional[float] = Field(None, gt=0)


class Fluid(Model):
    name: str = "air"
    temperature_c: float = 20.0
    density: float = Field(1.204, gt=0)
    kinematic_viscosity: float = Field(1.516e-5, gt=0)


class WallTreatment(str, Enum):
    WALL_FUNCTIONS = "wall_functions"
    LOW_RE = "low_re"


class PhysicsConfig(Model):
    regime: str = Field("steady_incompressible", description="Only steady incompressible RANS is generated in this release")
    turbulence_model: TurbulenceModel = TurbulenceModel.K_OMEGA_SST
    fluid: Fluid = Field(default_factory=Fluid)
    inlet_turbulence: InletTurbulence = Field(default_factory=InletTurbulence)
    wall_treatment: WallTreatment = WallTreatment.WALL_FUNCTIONS
