"""OpenFOAM Foundation adapter (v13/v14) — EXPERIMENTAL, not validated.

Known semantic differences from OpenCFD that this adapter encapsulates:

* solver: ``foamRun -solver incompressibleFluid`` (application ``foamRun``) instead of
  ``simpleFoam``; the SIMPLE/relaxation dictionaries are compatible in spirit but some
  keywords differ (verify before enabling).
* ``constant/momentumTransport`` instead of ``turbulenceProperties`` (``model`` keyword
  in the RAS sub-dictionary).
* ``constant/physicalProperties`` with ``viscosityModel constant;`` instead of
  ``transportProperties``.
* function objects: ``forces``/``forceCoeffs`` take ``CofR``, ``dragDir``, ``liftDir``,
  ``pitchAxis``, ``magUInf``, ``lRef``, ``Aref``; ``solverInfo`` is ``residuals``.

The application refuses to run this adapter unless the user explicitly enables
experimental adapters; golden-file tests document its output.
"""

from __future__ import annotations

from typing import Any

from ..ast import Dict, vec
from .opencfd import OpenCFDAdapter


class FoundationAdapter(OpenCFDAdapter):
    fork = "foundation"
    validated = False

    def __init__(self, version: str = "14"):
        super().__init__(version)
        self.version = version

    def steady_solver_argv(self) -> list[str]:
        return ["foamRun", "-solver", "incompressibleFluid"]

    def application_name(self) -> str:
        return "foamRun"

    def potential_init_argv(self) -> list[str]:
        return ["potentialFoam", "-writePhi"]  # Foundation spelling of the flag

    def transport_file(self, nu: float) -> tuple[str, Dict]:
        return "constant/physicalProperties", Dict(viscosityModel="constant", nu=float(nu))

    def turbulence_file(self, model: str, coeffs: dict[str, Any] | None = None) -> tuple[str, Dict]:
        d = Dict()
        if model == "laminar":
            d["simulationType"] = "laminar"
        else:
            d["simulationType"] = "RAS"
            ras = Dict(model=model, turbulence=True, printCoeffs=True)
            if coeffs:
                ras[f"{model}Coeffs"] = Dict(coeffs)
            d["RAS"] = ras
        return "constant/momentumTransport", d

    def force_coeffs_fo(self, patches, rho, cofr, e1, e3, mag_u, l_ref, a_ref) -> Dict:
        import numpy as np

        pitch = np.cross(np.asarray(e3, float), np.asarray(e1, float))
        return Dict(
            type="forceCoeffs",
            libs=["libforces.so"],
            writeControl="timeStep",
            writeInterval=1,
            patches=list(patches),
            rho="rhoInf",
            rhoInf=float(rho),
            CofR=vec(cofr),
            dragDir=vec(e1),
            liftDir=vec(e3),
            pitchAxis=vec(pitch),
            magUInf=float(mag_u),
            lRef=float(l_ref),
            Aref=float(a_ref),
        )

    def solver_info_fo(self, fields) -> Dict:
        return Dict(type="residuals", libs=["libutilityFunctionObjects.so"], fields=list(fields))
