"""Design exploration: geometry parameters, design variables, studies, optimisation."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import Field, model_validator

from .common import Model, Vec3

# --------------------------------------------------------------------------- geometry parameters


class TranslateDef(Model):
    kind: Literal["translate"] = "translate"
    direction: Vec3 = (0.0, 0.0, 1.0)


class RotateDef(Model):
    kind: Literal["rotate"] = "rotate"
    pivot: Vec3
    axis: Vec3 = (0.0, 1.0, 0.0)


class FFDDef(Model):
    """Trivariate Bernstein FFD lattice; the parameter value scales a displacement pattern."""

    kind: Literal["ffd"] = "ffd"
    box_min: Vec3
    box_max: Vec3
    divisions: tuple[int, int, int] = (3, 3, 3)
    # Control points moved by this parameter: (i, j, k) -> displacement direction per unit value
    moves: list[tuple[tuple[int, int, int], Vec3]] = Field(default_factory=list)
    frozen_boundary: bool = Field(True, description="Keep outer lattice faces fixed for C0 continuity")


class RBFDef(Model):
    """Radial-basis deformation: control handles displaced per unit value, others fixed."""

    kind: Literal["rbf"] = "rbf"
    handles: list[tuple[Vec3, Vec3]] = Field(default_factory=list, description="(point, displacement per unit value)")
    fixed_points: list[Vec3] = Field(default_factory=list)
    support_radius: float = Field(0.5, gt=0)
    basis: Literal["wendland_c2", "thin_plate"] = "wendland_c2"


class ZoneDef(Model):
    """One shape freedom of a shape zone (see :class:`ShapeZone`): the surface inside the zone
    box moves along ``direction``, fading smoothly (C2) to zero over ``blend`` inside the box
    faces, so nothing outside the box moves. With ``segments`` > 1 the zone is split along x
    into overlapping smooth sections that move independently (they sum to a uniform move).

    ``x`` / ``z``: translation; ``width``: lateral scaling about the centreline (outwards for
    positive values, reaching the full value at ``half_width`` from it)."""

    kind: Literal["zone"] = "zone"
    zone: str
    box_min: Vec3
    box_max: Vec3
    blend: float = Field(0.1, gt=0)
    direction: Literal["x", "z", "width"] = "z"
    segments: int = Field(1, ge=1, le=5)
    segment: int = Field(0, ge=0)
    half_width: float = Field(1.0, gt=0, description="Reference half width for 'width' (m)")
    metres_per_unit: float = Field(0.001, gt=0, description="Parameter values are in mm by default")

    @model_validator(mode="after")
    def _check(self):
        if any(b <= a for a, b in zip(self.box_min, self.box_max)):
            raise ValueError(f"zone {self.zone}: the box must have positive size in x, y and z")
        if self.segment >= self.segments:
            raise ValueError(f"zone {self.zone}: segment {self.segment} of {self.segments}")
        return self


DeformationDef = Union[TranslateDef, RotateDef, FFDDef, RBFDef, ZoneDef]


class GeometryParameter(Model):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str
    units: str = "m"
    baseline: float = 0.0
    lower: float
    upper: float
    step: Optional[float] = None
    components: list[str] = Field(default_factory=list, description="Affected component ids (empty = all non-wheel)")
    frozen_components: list[str] = Field(default_factory=list)
    deformation: DeformationDef = Field(discriminator="kind")
    max_displacement: Optional[float] = Field(None, gt=0, description="Reject designs moving any vertex further (m)")
    symmetric_about_centreline: bool = False

    @model_validator(mode="after")
    def _bounds(self):
        if not self.lower <= self.baseline <= self.upper:
            raise ValueError(f"parameter {self.id}: baseline must lie within [lower, upper]")
        return self


# --------------------------------------------------------------------------- shape zones


class ZoneMove(Model):
    direction: Literal["x", "z", "width"] = "z"
    max_mm: float = Field(30.0, gt=0, le=1000, description="Largest movement either way (mm)")
    segments: int = Field(1, ge=1, le=5, description="Independent sections along the car")


class ShapeZone(Model):
    """A region of the vehicle the optimiser may reshape; everything outside stays exactly as
    imported. Each move becomes one geometry parameter per section (``param.<zone>_<dir>[k]``)
    usable as a study or optimisation variable."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    name: str
    preset: str = "custom"
    box_min: Vec3
    box_max: Vec3
    blend: float = Field(0.1, gt=0, description="Distance over which the change fades out inside the box (m)")
    components: list[str] = Field(default_factory=list, description="Parts reshaped (empty = all except wheels)")
    moves: list[ZoneMove] = Field(default_factory=lambda: [ZoneMove()])
    symmetric: bool = Field(True, description="Mirror-symmetric about the centreline")
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self):
        if any(b <= a for a, b in zip(self.box_min, self.box_max)):
            raise ValueError(f"zone '{self.name}': the box must have positive size in x, y and z")
        dirs = [m.direction for m in self.moves]
        if len(dirs) != len(set(dirs)):
            raise ValueError(f"zone '{self.name}': each direction can be used once")
        return self


# --------------------------------------------------------------------------- design variables


class VariableKind(str, Enum):
    CONTINUOUS = "continuous"
    INTEGER = "integer"
    DISCRETE = "discrete"


class DesignVariable(Model):
    """A study/optimisation variable bound to a configuration target.

    Targets: ``operating.speed``, ``operating.yaw_deg``, ``vehicle.ride_height_offset``,
    ``vehicle.rake_deg``, ``vehicle.front_ride_height``, ``vehicle.rear_ride_height``,
    ``param.<geometry-parameter-id>``, ``mesh.base_cell_size``, ``numerics.iterations``,
    ``geometry.variant`` (discrete: geometry revision ids).
    """

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    target: str
    kind: VariableKind = VariableKind.CONTINUOUS
    lower: Optional[float] = None
    upper: Optional[float] = None
    values: Optional[list[Union[float, str]]] = None
    baseline: Optional[Union[float, str]] = None
    units: str = ""
    label: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.kind == VariableKind.DISCRETE:
            if not self.values:
                raise ValueError(f"variable {self.id}: discrete variables need 'values'")
        else:
            if self.lower is None or self.upper is None or not self.lower < self.upper:
                raise ValueError(f"variable {self.id}: need lower < upper")
        return self


# --------------------------------------------------------------------------- studies


class StudyKind(str, Enum):
    ONE_FACTOR = "one_factor"
    FULL_FACTORIAL = "full_factorial"
    LATIN_HYPERCUBE = "latin_hypercube"
    TABLE = "table"


class Scheduling(Model):
    concurrency: int = Field(1, ge=1)
    cores_per_run: int = Field(1, ge=1)
    max_retries: int = Field(1, ge=0, le=5)
    reuse_meshes: bool = True
    accept_unconverged: bool = Field(True, description="Use designs whose runs finished without meeting the convergence "
                                     "criteria (flagged); if false they count as failed evaluations")


class StudyDefinition(Model):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    name: str
    kind: StudyKind
    variables: list[DesignVariable]
    levels: dict[str, list[Union[float, str]]] = Field(default_factory=dict, description="Per-variable levels for sweeps/factorials")
    n_samples: int = Field(10, ge=1)
    seed: int = 0
    table: list[dict[str, Union[float, str]]] = Field(default_factory=list)
    include_baseline: bool = True
    scheduling: Scheduling = Field(default_factory=Scheduling)
    metrics: list[str] = Field(default_factory=lambda: ["Cd", "Cl", "balance_front_pct"])


# --------------------------------------------------------------------------- optimisation


class Sense(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class Objective(Model):
    metric: str
    sense: Sense = Sense.MINIMIZE
    aggregate: Literal["single", "mean", "max", "min"] = "single"


class ConstraintBound(str, Enum):
    ABSOLUTE = "absolute"
    BASELINE_DELTA = "baseline_delta"  # metric <op> baseline + value
    BASELINE_RATIO = "baseline_ratio"  # metric <op> baseline * value


class Constraint(Model):
    metric: str
    op: Literal[">=", "<="]
    value: float
    bound: ConstraintBound = ConstraintBound.ABSOLUTE


class Algorithm(str, Enum):
    SPACE_FILLING = "space_filling"
    GP_BO = "gp_bo"
    NSGA2 = "nsga2"
    NELDER_MEAD = "nelder_mead"


class OptimisationDefinition(Model):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    name: str
    variables: list[DesignVariable]
    objectives: list[Objective] = Field(min_length=1)
    constraints: list[Constraint] = Field(default_factory=list)
    algorithm: Algorithm = Algorithm.GP_BO
    budget: int = Field(30, ge=1, description="Maximum number of design evaluations")
    batch_size: int = Field(1, ge=1, description="Candidates evaluated concurrently")
    initial_samples: Optional[int] = Field(None, ge=1)
    seed: int = 0
    conditions: list[dict[str, float]] = Field(
        default_factory=list, description="Operating conditions for multi-condition objectives, e.g. [{'operating.yaw_deg': 0}, {...: 5}]"
    )
    scheduling: Scheduling = Field(default_factory=Scheduling)
    evaluate_baseline: bool = True

    @model_validator(mode="after")
    def _check(self):
        if len(self.objectives) > 1 and self.algorithm in (Algorithm.GP_BO, Algorithm.NELDER_MEAD):
            raise ValueError("multi-objective optimisation requires 'nsga2' or 'space_filling'")
        needs_baseline = any(c.bound != ConstraintBound.ABSOLUTE for c in self.constraints)
        if needs_baseline and not self.evaluate_baseline:
            raise ValueError("baseline-relative constraints require evaluate_baseline=true")
        return self
