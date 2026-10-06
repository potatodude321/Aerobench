"""OpenCFD (ESI/Keysight) OpenFOAM v2606 adapter — the validated primary target."""

from __future__ import annotations

from typing import Any

from ..ast import Dict, vec
from .base import ForkAdapter


class OpenCFDAdapter(ForkAdapter):
    fork = "opencfd"
    validated = True

    def __init__(self, version: str = "v2606"):
        self.version = version

    def steady_solver_argv(self) -> list[str]:
        return ["simpleFoam"]

    def application_name(self) -> str:
        return "simpleFoam"

    def transport_file(self, nu: float) -> tuple[str, Dict]:
        return "constant/transportProperties", Dict(transportModel="Newtonian", nu=float(nu))

    def turbulence_file(self, model: str, coeffs: dict[str, Any] | None = None) -> tuple[str, Dict]:
        d = Dict()
        if model == "laminar":
            d["simulationType"] = "laminar"
        else:
            d["simulationType"] = "RAS"
            ras = Dict(RASModel=model, turbulence=True, printCoeffs=True)
            if coeffs:
                ras[f"{model}Coeffs"] = Dict(coeffs)
            d["RAS"] = ras
        return "constant/turbulenceProperties", d

    def forces_fo(self, patches, rho, cofr) -> Dict:
        return Dict(
            type="forces",
            libs=["forces"],
            writeControl="timeStep",
            writeInterval=1,
            log=False,
            patches=list(patches),
            rho="rhoInf",
            rhoInf=float(rho),
            CofR=vec(cofr),
        )

    def force_coeffs_fo(self, patches, rho, cofr, e1, e3, mag_u, l_ref, a_ref) -> Dict:
        return Dict(
            type="forceCoeffs",
            libs=["forces"],
            writeControl="timeStep",
            writeInterval=1,
            log=True,
            patches=list(patches),
            rho="rhoInf",
            rhoInf=float(rho),
            origin=vec(cofr),
            e1=vec(e1),
            e3=vec(e3),
            magUInf=float(mag_u),
            lRef=float(l_ref),
            Aref=float(a_ref),
        )

    def solver_info_fo(self, fields) -> Dict:
        return Dict(type="solverInfo", libs=["utilityFunctionObjects"], fields=list(fields), writeResidualFields=False)

    def yplus_fo(self) -> Dict:
        return Dict(type="yPlus", libs=["fieldFunctionObjects"], writeControl="writeTime")

    def wall_shear_fo(self, patches) -> Dict:
        return Dict(type="wallShearStress", libs=["fieldFunctionObjects"], patches=list(patches), writeControl="writeTime")

    def pressure_coeff_fo(self, u_inf) -> Dict:
        return Dict(
            type="pressure",
            libs=["fieldFunctionObjects"],
            mode="staticCoeff",
            result="Cp",
            rho="rhoInf",
            rhoInf=1.0,  # kinematic pressure: rho cancels in the coefficient
            pInf=0.0,
            UInf=vec(u_inf),
            writeControl="writeTime",
        )

    def continuity_fo(self) -> Dict:
        return Dict(type="continuityError", libs=["fieldFunctionObjects"], writeControl="timeStep", writeInterval=1)

    def abort_fo(self) -> Dict:
        # trigger file "<case>/aerobenchStop" (name of this function object); content action=...
        return Dict(type="abort", libs=["utilityFunctionObjects"], action="writeNow")
