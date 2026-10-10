"""Structural check of aero elements (wings, splitters, flaps): materials, sections (sheet
thickness or composite layup), supports and load settings for the CalculiX analysis that
uses the surface loads of a finished CFD run (ADR-0012).

Units: SI throughout (Pa, m, kg/m³); ply angles in degrees.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import Field, model_validator

from .common import Model, Vec3
from .mesh import BoxShape, CylinderShape, SphereShape


class MaterialKind(str, Enum):
    ISOTROPIC = "isotropic"  # metals, plastics: E, nu, yield/ultimate strength
    LAMINA = "lamina"  # one ply of a fibre composite (unidirectional tape or woven fabric)


class Material(Model):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,60}$")
    name: str
    kind: MaterialKind
    density: float = Field(gt=0, description="kg/m³")
    # isotropic
    E: Optional[float] = Field(None, gt=0, description="Young's modulus (Pa)")
    nu: Optional[float] = Field(None, gt=-1, lt=0.5, description="Poisson's ratio")
    yield_strength: Optional[float] = Field(None, gt=0, description="Pa")
    ultimate_strength: Optional[float] = Field(None, gt=0, description="Pa")
    # lamina (in-plane; through-thickness values are derived when not given)
    E1: Optional[float] = Field(None, gt=0, description="Fibre-direction modulus (Pa)")
    E2: Optional[float] = Field(None, gt=0, description="Transverse modulus (Pa)")
    G12: Optional[float] = Field(None, gt=0, description="In-plane shear modulus (Pa)")
    nu12: Optional[float] = Field(None, gt=-1, lt=1)
    E3: Optional[float] = Field(None, gt=0)
    G13: Optional[float] = Field(None, gt=0)
    G23: Optional[float] = Field(None, gt=0)
    nu23: Optional[float] = Field(None, gt=-1, lt=1)
    Xt: Optional[float] = Field(None, gt=0, description="Tensile strength along the fibres (Pa)")
    Xc: Optional[float] = Field(None, gt=0, description="Compressive strength along the fibres (Pa), positive")
    Yt: Optional[float] = Field(None, gt=0, description="Transverse tensile strength (Pa)")
    Yc: Optional[float] = Field(None, gt=0, description="Transverse compressive strength (Pa), positive")
    S12: Optional[float] = Field(None, gt=0, description="In-plane shear strength (Pa)")
    ply_thickness: Optional[float] = Field(None, gt=0, description="Typical cured ply thickness (m)")
    source: str = Field("", description="Where the values come from")
    builtin: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.kind == MaterialKind.ISOTROPIC:
            missing = [k for k in ("E", "nu", "yield_strength") if getattr(self, k) is None]
        else:
            missing = [k for k in ("E1", "E2", "G12", "nu12", "Xt", "Xc", "Yt", "Yc", "S12") if getattr(self, k) is None]
        if missing:
            raise ValueError(f"material '{self.name}': missing {', '.join(missing)}")
        if self.kind == MaterialKind.LAMINA:
            e = self.engineering_constants()
            E1, E2, E3, n12, n13, n23 = e["E1"], e["E2"], e["E3"], e["nu12"], e["nu13"], e["nu23"]
            n21, n31, n32 = n12 * E2 / E1, n13 * E3 / E1, n23 * E3 / E2
            det = 1 - n12 * n21 - n23 * n32 - n13 * n31 - 2 * n21 * n32 * n13
            if not (det > 0 and abs(n12) < math.sqrt(E1 / E2) and abs(n23) < math.sqrt(E2 / E3)):
                raise ValueError(f"material '{self.name}': the elastic constants are not physically possible "
                                 "(stiffness matrix not positive definite)")
        return self

    def engineering_constants(self) -> dict[str, float]:
        """Complete 3D orthotropic constants for a lamina (transversely isotropic defaults)."""
        E2 = float(self.E2)  # type: ignore[arg-type]
        nu23 = self.nu23 if self.nu23 is not None else 0.45
        E3 = self.E3 if self.E3 is not None else E2
        return {"E1": float(self.E1), "E2": E2, "E3": float(E3), "nu12": float(self.nu12), "nu13": float(self.nu12),  # type: ignore[arg-type]
                "nu23": float(nu23), "G12": float(self.G12), "G13": float(self.G13 or self.G12),  # type: ignore[arg-type]
                "G23": float(self.G23 or E3 / (2 * (1 + nu23)))}


class Ply(Model):
    material: str
    thickness: float = Field(gt=0, le=0.05, description="m")
    angle: float = Field(0.0, ge=-90, le=90, description="Degrees from the 0° direction, about the outward normal")


class SectionKind(str, Enum):
    SHEET = "sheet"  # one isotropic material of a given thickness
    LAMINATE = "laminate"  # composite plies


class Section(Model):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    name: str
    components: list[str] = Field(default_factory=list, description="Parts it applies to (empty = every analysed part)")
    kind: SectionKind = SectionKind.LAMINATE
    material: Optional[str] = None
    thickness: Optional[float] = Field(None, gt=0, le=0.2, description="Sheet thickness (m)")
    plies: list[Ply] = Field(default_factory=list, description="From the outer (aerodynamic) surface inwards")
    symmetric: bool = Field(False, description="Mirror the plies about the mid-plane (the list is the outer half)")
    zero_direction: Vec3 = Field((0.0, 1.0, 0.0), description="0° fibre direction (projected onto the surface)")

    @model_validator(mode="after")
    def _check(self):
        if self.kind == SectionKind.SHEET and (self.material is None or self.thickness is None):
            raise ValueError(f"section '{self.name}': a sheet needs a material and a thickness")
        if self.kind == SectionKind.LAMINATE and not self.plies:
            raise ValueError(f"section '{self.name}': a laminate needs at least one ply")
        if math.hypot(*self.zero_direction) < 1e-9:
            raise ValueError(f"section '{self.name}': the 0° direction must not be zero")
        return self

    def stack(self) -> list[Ply]:
        """All plies from the outer surface inwards (symmetric layups expanded)."""
        return list(self.plies) + (list(reversed(self.plies)) if self.symmetric else [])

    def total_thickness(self) -> float:
        return float(self.thickness or 0.0) if self.kind == SectionKind.SHEET else sum(p.thickness for p in self.stack())


SupportShape = Union[BoxShape, SphereShape, CylinderShape]


class Support(Model):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    name: str
    shape: SupportShape = Field(discriminator="kind")
    enabled: bool = True


class StructureModel(str, Enum):
    SKIN = "skin"  # the whole outer surface is a shell (hollow or foam-filled elements, skins)
    PLATE = "plate"  # one side of a thin closed part is the shell (plates: splitters, endplates)


class StructureConfig(Model):
    model: StructureModel = StructureModel.SKIN
    plate_side: Literal["upper", "lower"] = "upper"
    components: list[str] = Field(default_factory=list, description="Parts analysed (empty = all except wheels and stilts)")
    sections: list[Section] = Field(default_factory=list)
    supports: list[Support] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list, description="Materials defined in this project")
    run_id: Optional[str] = Field(None, description="CFD run whose surface loads are applied")
    design_speed: Optional[float] = Field(None, gt=0, description="Scale the loads to this speed (m/s); None = the run's speed")
    load_factor: float = Field(1.0, gt=0, le=20, description="Extra factor on the aerodynamic loads (kerbs, gusts)")
    self_weight: bool = True
    gravity: float = Field(9.81, ge=0)
    required_reserve: float = Field(1.5, ge=1.0, le=10, description="Smallest acceptable reserve factor")
    max_elements: int = Field(30000, ge=500, le=400000, description="Surface triangles of the structural mesh")
