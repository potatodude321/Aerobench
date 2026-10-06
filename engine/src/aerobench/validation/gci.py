"""Grid-convergence index (Roache; procedure of Celik et al., J. Fluids Eng. 130, 078001, 2008).

Given a quantity computed on three systematically refined meshes, estimate the observed
order of convergence, the Richardson-extrapolated value and the fine-grid GCI.  The
procedure flags oscillatory convergence and divergence instead of returning a misleading
number.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

from scipy.optimize import brentq


@dataclass
class GCIResult:
    status: str  # monotonic | oscillatory | divergent | insufficient
    h: tuple[float, float, float]  # representative cell sizes, fine -> coarse
    phi: tuple[float, float, float]  # values, fine -> coarse
    r21: Optional[float] = None
    r32: Optional[float] = None
    order: Optional[float] = None
    extrapolated: Optional[float] = None
    e21_rel: Optional[float] = None
    gci_fine: Optional[float] = None  # relative (fraction of phi1)
    gci_coarse: Optional[float] = None
    asymptotic_ratio: Optional[float] = None  # ≈ 1 in the asymptotic range
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def gci(h: list[float], phi: list[float], safety: float = 1.25) -> GCIResult:
    """``h`` and ``phi`` for three meshes in any order; ``h`` is the representative cell size
    (e.g. background cell size, or (V/N)^(1/3)).  Uses Fs = 1.25 for three-grid studies."""
    if len(h) != 3 or len(phi) != 3:
        raise ValueError("the GCI needs exactly three meshes")
    order_idx = sorted(range(3), key=lambda i: h[i])
    h1, h2, h3 = (float(h[i]) for i in order_idx)
    p1, p2, p3 = (float(phi[i]) for i in order_idx)
    res = GCIResult("insufficient", (h1, h2, h3), (p1, p2, p3))
    if not (h1 < h2 < h3):
        res.note = "Cell sizes must be distinct."
        return res
    r21, r32 = h2 / h1, h3 / h2
    res.r21, res.r32 = r21, r32
    if r21 < 1.1 or r32 < 1.1:
        res.note = "Refinement ratios below 1.1: differences are dominated by noise (Celik et al. recommend r > 1.3)."
    e21, e32 = p2 - p1, p3 - p2
    if e21 == 0 or e32 == 0:
        res.status = "insufficient"
        res.note = (res.note + " Two meshes give identical values; the order cannot be estimated.").strip()
        return res
    s = math.copysign(1.0, e32 / e21)
    if e32 / e21 < 0:
        res.status = "oscillatory"
        res.note = (res.note + " The solution oscillates between meshes; no order of convergence can be inferred.").strip()
        res.e21_rel = abs(e21 / p1) if p1 else None
        return res

    def f(p: float) -> float:
        q = math.log((r21**p - s) / (r32**p - s))
        return p - abs(math.log(abs(e32 / e21)) + q) / math.log(r21)

    # fixed-point form solved as a root; observed orders outside (0.05, 12) are not credible
    try:
        p = brentq(f, 0.05, 12.0)
    except ValueError:
        res.status = "divergent" if abs(e32) < abs(e21) else "insufficient"
        res.note = (res.note + " No observed order in (0.05, 12): the meshes are not in the asymptotic range.").strip()
        return res
    res.order = p
    if abs(e32) < abs(e21):
        res.status = "divergent"
        res.note = (res.note + " Differences grow with refinement.").strip()
        return res
    res.status = "monotonic"
    res.extrapolated = (r21**p * p1 - p2) / (r21**p - 1)
    res.e21_rel = abs(e21 / p1) if p1 else None
    if res.e21_rel is not None:
        res.gci_fine = safety * res.e21_rel / (r21**p - 1)
        e32_rel = abs(e32 / p2) if p2 else None
        if e32_rel is not None:
            res.gci_coarse = safety * e32_rel / (r32**p - 1)
            if res.gci_fine:
                res.asymptotic_ratio = res.gci_coarse / (r21**p * res.gci_fine)
    return res
