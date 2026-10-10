"""Built-in material library and the user's own library (shared by all projects).

Values are typical published properties for preliminary design: they are not design
allowables. For a part that will be built, use the supplier's data sheet or test values
(create a material of your own with them). Strengths of composites are lamina strengths of
a cured ply at room temperature, dry.

Sources (typical values, rounded):
* aluminium, steel, titanium: ASM Handbook / MMPDS typical properties;
* plastics: manufacturers' data sheets (injection-moulded or SLS, as noted);
* composites: Daniel & Ishai, "Engineering Mechanics of Composite Materials" (2006), and
  prepreg / fabric data sheets of common carbon, glass and aramid systems.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..domain.structure import Material, MaterialKind

GPa, MPa = 1e9, 1e6


def _iso(id_: str, name: str, rho: float, E: float, nu: float, sy: float, su: float, source: str) -> Material:
    return Material(id=id_, name=name, kind=MaterialKind.ISOTROPIC, density=rho, E=E * GPa, nu=nu, yield_strength=sy * MPa,
                    ultimate_strength=su * MPa, source=source, builtin=True)


def _ply(id_: str, name: str, rho: float, E1: float, E2: float, G12: float, nu12: float, Xt: float, Xc: float, Yt: float, Yc: float,
         S12: float, t_mm: float, source: str, E3: float | None = None, nu23: float | None = None) -> Material:
    return Material(id=id_, name=name, kind=MaterialKind.LAMINA, density=rho, E1=E1 * GPa, E2=E2 * GPa, G12=G12 * GPa, nu12=nu12,
                    E3=E3 * GPa if E3 else None, nu23=nu23, Xt=Xt * MPa, Xc=Xc * MPa, Yt=Yt * MPa, Yc=Yc * MPa, S12=S12 * MPa,
                    ply_thickness=t_mm / 1000.0, source=source, builtin=True)


BUILTIN: list[Material] = [
    _iso("al_6061_t6", "Aluminium 6061-T6", 2700, 68.9, 0.33, 276, 310, "ASM typical; sheet and extrusions"),
    _iso("al_7075_t6", "Aluminium 7075-T6", 2810, 71.7, 0.33, 503, 572, "ASM typical; plate"),
    _iso("al_5083_h111", "Aluminium 5083-H111", 2660, 71.0, 0.33, 145, 275, "EN 485-2 typical; sheet"),
    _iso("steel_s355", "Steel S355", 7850, 210.0, 0.30, 355, 490, "EN 10025-2 minimum values"),
    _iso("steel_4130", "Steel AISI 4130, normalised", 7850, 205.0, 0.29, 460, 670, "ASM typical; tube"),
    _iso("stainless_304", "Stainless steel 304, annealed", 8000, 193.0, 0.29, 215, 505, "ASM typical"),
    _iso("ti_6al4v", "Titanium Ti-6Al-4V, annealed", 4430, 113.8, 0.342, 880, 950, "ASM typical"),
    _iso("pa12_sls", "Nylon PA12, laser sintered", 1010, 1.7, 0.39, 48, 48, "SLS data sheets (yield taken as tensile strength)"),
    _iso("pa6_gf30", "Nylon PA6 GF30, moulded (dry)", 1360, 9.5, 0.35, 180, 180, "Data sheets, dry as moulded"),
    _iso("abs", "ABS, moulded", 1050, 2.2, 0.35, 40, 40, "Data sheets"),
    _iso("petg", "PETG, 3D printed", 1270, 2.0, 0.38, 50, 50, "Data sheets (printed in the load direction)"),
    _iso("pc", "Polycarbonate", 1200, 2.35, 0.37, 62, 65, "Data sheets"),
    _ply("cfrp_ud_t700", "Carbon/epoxy UD prepreg (T700 class)", 1550, 132, 9.0, 5.0, 0.30, 2100, 1200, 50, 200, 80, 0.15,
         "Prepreg data sheets; Daniel & Ishai"),
    _ply("cfrp_ud_hm", "Carbon/epoxy UD prepreg, high modulus (M40J class)", 1600, 220, 7.0, 4.5, 0.30, 2000, 900, 40, 170, 70, 0.15,
         "Prepreg data sheets"),
    _ply("cfrp_twill_t300", "Carbon/epoxy 2x2 twill, 200 g/m² (T300 3K)", 1500, 60, 60, 4.5, 0.05, 600, 570, 600, 570, 90, 0.22,
         "Fabric prepreg data sheets", E3=9.0, nu23=0.30),
    _ply("gfrp_ud_e", "E-glass/epoxy UD", 1950, 40, 9.0, 3.5, 0.28, 1000, 600, 30, 120, 60, 0.20, "Daniel & Ishai"),
    _ply("gfrp_woven_e", "E-glass/epoxy woven, 300 g/m²", 1850, 22, 22, 3.5, 0.13, 350, 300, 350, 300, 60, 0.25,
         "Fabric data sheets", E3=9.0, nu23=0.30),
    _ply("afrp_woven_k49", "Aramid (Kevlar 49)/epoxy woven", 1350, 30, 30, 2.0, 0.20, 500, 150, 500, 150, 50, 0.25,
         "Fabric data sheets", E3=5.5, nu23=0.30),
    _ply("flax_ud", "Flax/epoxy UD", 1300, 30, 3.5, 1.9, 0.33, 300, 130, 20, 90, 35, 0.25, "Natural-fibre composite literature"),
]


def user_library_path() -> Path:
    from ..settings import config_dir

    return Path(config_dir()) / "materials.json"


def load_user_library() -> list[Material]:
    p = user_library_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for m in data.get("materials", []):
        try:
            out.append(Material.model_validate({**m, "builtin": False}))
        except ValueError:
            continue  # a damaged entry must not hide the rest
    return out


def save_user_library(materials: Iterable[Material]) -> None:
    from ..core.fs import atomic_write_text

    p = user_library_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps({"format": 1, "materials": [m.model_dump(mode="json", exclude={"builtin"}) for m in materials]}, indent=2))


def all_materials(project_materials: Iterable[Material] = ()) -> dict[str, Material]:
    """id -> material; project materials override the user library, which overrides built-ins."""
    out = {m.id: m for m in BUILTIN}
    out.update({m.id: m for m in load_user_library()})
    out.update({m.id: m.model_copy(update={"builtin": False}) for m in project_materials})
    return out
