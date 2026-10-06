"""Fork/version compatibility adapters (ADR-0006).

The adapter owns everything whose *syntax or semantics* differs between OpenFOAM forks
and versions.  The case builder asks the adapter; it never hard-codes fork details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..ast import Dict


class ForkAdapter(ABC):
    fork: str = ""
    version: str = ""
    validated: bool = False

    # ------------------------------------------------------------------ applications
    @abstractmethod
    def steady_solver_argv(self) -> list[str]:
        """Command (without -case/-parallel) running the steady incompressible solver."""

    @abstractmethod
    def application_name(self) -> str:
        """Value for controlDict 'application'."""

    def potential_init_argv(self) -> list[str]:
        return ["potentialFoam", "-writephi"]

    # ------------------------------------------------------------------ constant/
    @abstractmethod
    def transport_file(self, nu: float) -> tuple[str, Dict]:
        ...

    @abstractmethod
    def turbulence_file(self, model: str, coeffs: dict[str, Any] | None = None) -> tuple[str, Dict]:
        ...

    # ------------------------------------------------------------------ function objects
    @abstractmethod
    def forces_fo(self, patches: list[str], rho: float, cofr) -> Dict:
        ...

    @abstractmethod
    def force_coeffs_fo(self, patches: list[str], rho: float, cofr, e1, e3, mag_u: float, l_ref: float, a_ref: float) -> Dict:
        ...

    @abstractmethod
    def solver_info_fo(self, fields: list[str]) -> Dict:
        ...

    @abstractmethod
    def yplus_fo(self) -> Dict:
        ...

    @abstractmethod
    def wall_shear_fo(self, patches: list[str]) -> Dict:
        ...

    @abstractmethod
    def pressure_coeff_fo(self, u_inf) -> Dict:
        ...

    @abstractmethod
    def continuity_fo(self) -> Dict:
        ...

    @abstractmethod
    def abort_fo(self) -> Dict:
        ...

    def vorticity_fo(self) -> Dict:
        return Dict(type="vorticity", libs=["fieldFunctionObjects"], writeControl="writeTime")

    def q_fo(self) -> Dict:
        return Dict(type="Q", libs=["fieldFunctionObjects"], writeControl="writeTime")

    def probes_fo(self, points, fields) -> Dict:
        from ..ast import List, vec

        return Dict(type="probes", libs=["sampling"], writeControl="timeStep", writeInterval=10,
                    fields=list(fields), probeLocations=List([vec(p) for p in points]))

    # ------------------------------------------------------------------ boundary-condition types
    def bc_freestream_velocity(self, u) -> Dict:
        from ..ast import uniform

        return Dict(type="freestreamVelocity", freestreamValue=uniform(u))

    def bc_freestream_pressure(self, p: float) -> Dict:
        from ..ast import uniform

        return Dict(type="freestreamPressure", freestreamValue=uniform(p))

    def bc_rotating_wall(self, origin, axis, omega: float) -> Dict:
        from ..ast import uniform, vec

        return Dict(type="rotatingWallVelocity", origin=vec(origin), axis=vec(axis), omega=float(omega), value=uniform((0, 0, 0)))

    def describe(self) -> dict:
        return {"fork": self.fork, "version": self.version, "validated": self.validated}
