"""Prism-layer design from engineering targets (BUILD_SPEC §8.3)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..aero.flow import first_cell_height, yplus_from_height


@dataclass
class LayerDesign:
    mode: str  # "absolute" | "relative" | "none"
    n_layers: int
    expansion_ratio: float
    first_layer: float  # m (absolute) — for relative mode this is the estimate at the reference cell size
    final_layer: float  # m
    total_thickness: float  # m
    final_relative: float  # final / surface cell
    surface_cell: float  # m
    yplus_estimate: float
    assumptions: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def total_thickness(first: float, er: float, n: int) -> float:
    if abs(er - 1.0) < 1e-12:
        return first * n
    return first * (er**n - 1) / (er - 1)


def design_layers(
    target_yplus: float,
    speed: float,
    ref_length: float,
    nu: float,
    surface_cell: float,
    expansion_ratio: float = 1.2,
    final_relative_target: float = 0.5,
    n_layers: Optional[int] = None,
    max_layers: int = 15,
    correlation: str = "schlichting_1979",
) -> LayerDesign:
    """Pick the layer count so the final layer is ≈ ``final_relative_target`` × surface cell
    while the first layer meets the target y+ (absolute specification)."""
    ws = first_cell_height(target_yplus, speed, ref_length, nu, correlation=correlation)
    t1 = ws.first_cell_height
    warnings: list[str] = []
    er = expansion_ratio
    target_final = final_relative_target * surface_cell
    if n_layers is None:
        if t1 >= target_final:
            n = 1
            warnings.append(
                f"The first layer for y+={target_yplus:g} ({t1 * 1e3:.2f} mm) is not thinner than "
                f"{final_relative_target:g}× the surface cell ({surface_cell * 1e3:.1f} mm); a single layer is used. "
                "Refine the surface or raise the target y+."
            )
        else:
            n = 1 + math.log(target_final / t1) / math.log(er) if er > 1 else target_final / t1
            n = int(round(n))
            if n > max_layers:
                warnings.append(f"{n} layers would be needed for y+={target_yplus:g}; limited to {max_layers}. "
                                "Increase the expansion ratio or refine the surface.")
                n = max_layers
            n = max(n, 1)
    else:
        n = n_layers
    final = t1 * er ** (n - 1)
    T = total_thickness(t1, er, n)
    rel = final / surface_cell
    if rel > 0.8:
        warnings.append(f"Final layer is {rel:.2f}× the surface cell; layer collapse is likely near sharp edges.")
    if rel < 0.2:
        warnings.append(f"Final layer is only {rel:.2f}× the surface cell; the size jump to the outer mesh is large.")
    return LayerDesign(
        mode="absolute",
        n_layers=n,
        expansion_ratio=er,
        first_layer=t1,
        final_layer=final,
        total_thickness=T,
        final_relative=rel,
        surface_cell=surface_cell,
        yplus_estimate=target_yplus,
        assumptions=ws.assumptions,
        warnings=warnings,
    )


def relative_layers(
    n_layers: int,
    expansion_ratio: float,
    final_relative: float,
    surface_cell: float,
    speed: float,
    ref_length: float,
    nu: float,
    correlation: str = "schlichting_1979",
) -> LayerDesign:
    final = final_relative * surface_cell
    first = final / expansion_ratio ** (n_layers - 1)
    yp = yplus_from_height(first, speed, ref_length, nu, correlation)
    return LayerDesign(
        mode="relative",
        n_layers=n_layers,
        expansion_ratio=expansion_ratio,
        first_layer=first,
        final_layer=final,
        total_thickness=total_thickness(first, expansion_ratio, n_layers),
        final_relative=final_relative,
        surface_cell=surface_cell,
        yplus_estimate=yp,
        assumptions=f"Layers sized relative to the local surface cell; y+ estimated at the body surface cell "
        f"({surface_cell * 1e3:.1f} mm) with the {correlation} flat-plate correlation.",
    )
