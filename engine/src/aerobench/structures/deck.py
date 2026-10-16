"""CalculiX input deck for the structural check (linear static, S6 shells).

Laminates: CalculiX takes each ply's 0° axis as the projection of an orientation's x-axis
onto the shell at the element. On a curved part one global orientation would distort the
angles where the surface turns away from it, so the elements are grouped by normal
direction (bins about 10° wide) and every group gets orientations built from its mean
normal: 0° = the section's 0° direction projected onto that plane, +angles turning about the
outward normal. Plies are listed outer-first by the user and written inner-first, because
CalculiX stacks layers from the negative-normal side.

Loads: the aerodynamic loads arrive as consistent nodal forces on all six nodes of each
element (``loads.map_loads``); self-weight is written the same way (a uniform load on a
6-node triangle sits on its mid-edge nodes, a third each), so the deck knows every force
exactly. Forces on nodes held by a support go straight into the support without loading the
structure; they are left out of the deck and reported (``held_by_supports``), which also
keeps the check of support reactions against applied loads exact.

Supports: a support that takes in a patch of the surface (whole elements) holds its nodes
in translation, which clamps the patch through those elements and keeps the reported
support reactions exact. A support that only touches a line or a few nodes would be a
hinge that way, so its nodes are clamped in rotation as well (DOF 1-6); CalculiX passes
part of those reactions through the rotation constraints it generates and leaves them out
of the reaction totals, so the reaction check is skipped then (``reactions_exact``).

Offsets: in the skin model the laminate lies inside the outer (aerodynamic) surface, in the
plate model inside the part below the chosen face (OFFSET = 0.5: the nodes are on the top
surface of the shell). Where a section does not fit inside the tightest curves of its part
(a sharp leading edge, thicker than its radius), CalculiX could not expand it; that section
is centred on the surface instead (OFFSET = 0), and if even that folds over, the analysis is
refused before solving with the place named (``expansion``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.mesh import BoxShape, CylinderShape, SphereShape
from ..domain.structure import Material, MaterialKind, Section, SectionKind, StructureConfig
from .expansion import ISOLATE, plan_expansion
from .mesh import StructMesh


def _err(code: str, msg: str, action: str = "") -> AeroBenchError:
    return AeroBenchError.make(code, ErrorCategory.CONFIGURATION, msg, action=action)


PATCH_MIN_ELEMENTS = 4  # whole elements inside a support for it to hold a patch
TOO_THICK_SHARE = 0.25  # of a section's area expanded along own normals, above which a shell model is refused


def fibonacci_directions(n: int = 400) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def assign_sections(mesh: StructMesh, sections: list[Section]) -> np.ndarray:
    """Section index per triangle: a section naming a part wins over a section for all parts."""
    if not sections:
        raise _err("structure.no_section", "No section (material and thickness or layup) is defined.",
                   "Add a section on the Structure page.")
    out = -np.ones(mesh.n_tris, dtype=np.int64)
    explicit: dict[str, int] = {}
    for k, s in enumerate(sections):
        for c in s.components:
            if c in explicit:
                raise _err("structure.section_overlap", f"Part '{c}' is in two sections ('{sections[explicit[c]].name}' and '{s.name}').")
            explicit[c] = k
    catch_all = [k for k, s in enumerate(sections) if not s.components]
    if len(catch_all) > 1:
        raise _err("structure.section_overlap", "More than one section applies to all parts; name the parts of all but one.")
    for pi, pid in enumerate(mesh.part_ids):
        k = explicit.get(pid, catch_all[0] if catch_all else -1)
        out[mesh.part == pi] = k
    if (out < 0).any():
        missing = sorted({mesh.part_ids[i] for i in np.unique(mesh.part[out < 0])})
        raise _err("structure.section_missing", f"No section applies to: {', '.join(missing)}.", "Add a section for them.")
    return out


def support_nodes(nodes: np.ndarray, shape) -> np.ndarray:
    if isinstance(shape, BoxShape):
        lo, hi = np.asarray(shape.min), np.asarray(shape.max)
        return np.flatnonzero(np.all((nodes >= lo) & (nodes <= hi), axis=1))
    if isinstance(shape, SphereShape):
        return np.flatnonzero(np.linalg.norm(nodes - np.asarray(shape.centre), axis=1) <= shape.radius)
    if isinstance(shape, CylinderShape):
        a, b = np.asarray(shape.point1), np.asarray(shape.point2)
        ax = b - a
        L = float(np.linalg.norm(ax))
        if L == 0:
            return np.zeros(0, dtype=np.int64)
        ax /= L
        t = (nodes - a) @ ax
        r = np.linalg.norm((nodes - a) - np.outer(t, ax), axis=1)
        return np.flatnonzero((t >= 0) & (t <= L) & (r <= shape.radius))
    raise _err("structure.support_shape", f"Unsupported support shape {type(shape).__name__}.")


@dataclass
class DeckInfo:
    """What the result reader needs to interpret the solver output."""

    n_corner: int
    n_nodes: int
    element_section: list[int]  # per element (0-based)
    element_layers: list[list[str]] = field(default_factory=list)  # per section: material id per CalculiX layer (inner first)
    section_kinds: list[str] = field(default_factory=list)
    section_thickness: list[float] = field(default_factory=list)
    plies_outer_first: list[list[dict]] = field(default_factory=list)
    support_counts: dict[str, int] = field(default_factory=dict)
    support_kinds: dict[str, str] = field(default_factory=dict)  # "patch" (translations held) or "edge" (clamped)
    reactions_exact: bool = True
    mass: float = 0.0
    weight_vector: list[float] = field(default_factory=list)
    applied_total: list[float] = field(default_factory=list)
    held_by_supports: list[float] = field(default_factory=list)  # applied + weight on supported nodes (N)
    section_offsets: list[float] = field(default_factory=list)  # CalculiX OFFSET per section
    centred_sections: list[str] = field(default_factory=list)  # names of sections centred on the surface
    own_normal_elements: int = 0  # elements expanded along their own facet (they would fold along the smooth normals)
    own_normal_area_fraction: float = 0.0
    own_normal_example: list[float] = field(default_factory=list)  # a location of one of them (m)
    orientation_bins: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _fmt(v: float) -> str:
    return f"{v:.9g}"


def _id_lines(ids: np.ndarray, per: int = 16) -> str:
    ids = np.asarray(ids) + 1
    return "\n".join(", ".join(str(int(x)) for x in ids[i:i + per]) for i in range(0, len(ids), per)) + "\n"


def _check_every_piece_is_held(mesh: StructMesh, held: np.ndarray) -> None:
    """Each separate piece of the surface (parts that share no nodes are not joined in the shell
    model) needs a support of its own; one that has none would be free to fly away."""
    from .mesh import _shells

    lab = _shells(mesh.tris, len(mesh.points))
    held_corner = np.zeros(len(mesh.points), dtype=bool)
    held_corner[held[held < len(mesh.points)]] = True
    pieces = np.unique(lab)
    if len(pieces) < 2 and held_corner.any():
        return
    for k in pieces:
        faces = mesh.tris[lab == k]
        if held_corner[faces.reshape(-1)].any():
            continue
        names = sorted({mesh.part_ids[i] for i in np.unique(mesh.part[lab == k])}) if mesh.part_ids else []
        c = mesh.points[np.unique(faces)].mean(axis=0) * 1000.0
        what = f"'{', '.join(names)}'" if names else "A piece of the part"
        raise _err("structure.piece_not_held",
                   f"{what} (around x = {c[0]:.0f}, y = {c[1]:.0f}, z = {c[2]:.0f} mm) is a separate piece of the surface with no support: "
                   "it is not joined to the rest in the shell model, so nothing holds it.",
                   "Add a support where it is attached, leave it out of the check, or join it to the rest in your CAD model (one body).")


def _split_isolated(name: str, members: np.ndarray, colour: np.ndarray, offset: float):
    """The element set and, for its isolated elements, one per group whose offset differs by
    ISOLATE per group (so CalculiX gives them normals of their own)."""
    c = colour[members]
    out = []
    if (c < 0).any():
        out.append((name, members[c < 0], offset))
    for g in np.unique(c[c >= 0]):
        out.append((f"{name}_I{int(g)}", members[c == g], offset - ISOLATE * (int(g) + 1)))
    return out


def write_deck(path: Path, mesh: StructMesh, st: StructureConfig, materials: dict[str, Material], nodal_forces: np.ndarray,
               load_scale: float) -> DeckInfo:
    """Write ``path`` (.inp) and return the bookkeeping for the result reader."""
    nodes, conn = mesh.s6()
    n_corner = len(mesh.points)
    sec_of = assign_sections(mesh, st.sections)
    used_mats: dict[str, Material] = {}
    for s in st.sections:
        ids = [s.material] if s.kind == SectionKind.SHEET else [p.material for p in s.stack()]
        for mid in ids:
            m = materials.get(mid or "")
            if m is None:
                raise _err("structure.unknown_material", f"Section '{s.name}' uses an unknown material '{mid}'.")
            if s.kind == SectionKind.SHEET and m.kind != MaterialKind.ISOTROPIC:
                raise _err("structure.material_kind", f"Section '{s.name}': a sheet needs an isotropic material ('{m.name}' is a composite ply).",
                           "Use a laminate section for composite plies.")
            used_mats[m.id] = m
    supports = [sp for sp in st.supports if sp.enabled]
    if not supports:
        raise _err("structure.no_support", "The part is not held anywhere.", "Add at least one support (where it is bolted or bonded).")
    sup_sets: dict[str, np.ndarray] = {}
    for sp in supports:
        ids = support_nodes(nodes, sp.shape)
        if len(ids) < 3:
            raise _err("structure.support_empty", f"Support '{sp.name}' holds {len(ids)} node(s); it must contain part of the surface.",
                       "Move or enlarge it so that it overlaps the part where it is attached.")
        sup_sets[sp.id] = ids
    _check_every_piece_is_held(mesh, np.unique(np.concatenate(list(sup_sets.values()))))
    area = mesh.areas()
    normals = mesh.normals()
    info = DeckInfo(n_corner=n_corner, n_nodes=len(nodes), element_section=sec_of.tolist())
    per_area = np.zeros(mesh.n_tris)  # kg/m^2
    for k, s in enumerate(st.sections):
        sel = sec_of == k
        if s.kind == SectionKind.SHEET:
            info.element_layers.append([s.material])  # type: ignore[list-item]
            per_area[sel] = float(s.thickness) * used_mats[s.material].density  # type: ignore[index,arg-type]
        else:
            stack = s.stack()
            info.element_layers.append([p.material for p in reversed(stack)])
            per_area[sel] = sum(p.thickness * used_mats[p.material].density for p in stack)
        info.section_kinds.append(s.kind.value)
        info.section_thickness.append(s.total_thickness())
        info.plies_outer_first.append([p.model_dump() for p in s.stack()] if s.kind == SectionKind.LAMINATE else [])
    elem_mass = per_area * area
    info.mass = float(elem_mass.sum())
    g = float(st.gravity) if st.self_weight else 0.0
    info.weight_vector = [0.0, 0.0, -info.mass * g]
    if len(nodal_forces) != len(nodes):
        raise ValueError(f"nodal forces for {len(nodal_forces)} nodes, the mesh has {len(nodes)}")
    F = nodal_forces * load_scale
    info.applied_total = F.sum(0).tolist()
    if g > 0:
        for a in range(3, 6):
            np.add.at(F[:, 2], conn[:, a], -elem_mass * g / 3.0)
    all_sup = np.unique(np.concatenate(list(sup_sets.values())))
    info.held_by_supports = F[all_sup].sum(0).tolist()
    F[all_sup] = 0.0
    # nodes on the outer surface (skin) or on the chosen face (plate): material inside; an element
    # whose expansion would fold is isolated (its own normal, see expansion.py)
    plan = plan_expansion(nodes, conn, sec_of, info.section_thickness)
    if len(plan.still_folding):
        cent = mesh.centroids()[plan.still_folding]
        where = cent[np.argmin(cent[:, 0])] * 1000.0
        raise _err("structure.shell_folds",
                   f"{len(plan.still_folding)} element(s) of the structural mesh are degenerate and cannot be expanded through the "
                   f"thickness (e.g. near x = {where[0]:.0f}, y = {where[1]:.0f}, z = {where[2]:.0f} mm).",
                   "Repair the part's surface there (Geometry, Repair) and prepare the structural check again.")
    area_iso = np.bincount(sec_of[plan.isolated], weights=area[plan.isolated], minlength=len(st.sections))
    area_sec = np.bincount(sec_of, weights=area, minlength=len(st.sections))
    for k, sec in enumerate(st.sections):
        if area_sec[k] > 0 and area_iso[k] > TOO_THICK_SHARE * area_sec[k]:
            raise _err("structure.shell_too_thick",
                       f"Section '{sec.name}' ({sec.total_thickness() * 1000:.1f} mm) is thicker than the part is curved over "
                       f"{area_iso[k] / area_sec[k] * 100:.0f} % of its area: a shell model does not represent it there.",
                       "Use a thinner section or the plate model; a part this thick for its size needs a solid model.")
    info.section_offsets = list(plan.offsets)
    info.centred_sections = [st.sections[k].name for k in plan.centred]
    info.own_normal_elements = int(len(plan.isolated))
    info.own_normal_area_fraction = plan.isolated_area_fraction
    if len(plan.isolated):
        cent = mesh.centroids()[plan.isolated]
        info.own_normal_example = cent[np.argmin(cent[:, 0])].tolist()
    lines: list[str] = ["*HEADING", "AeroBench structural check (linear static, S6 shells)"]
    lines.append("*NODE, NSET=NALL")
    lines += [f"{i + 1}, {_fmt(p[0])}, {_fmt(p[1])}, {_fmt(p[2])}" for i, p in enumerate(nodes)]
    lines.append("*NSET, NSET=NCORNER")
    lines.append(_id_lines(np.arange(n_corner)).rstrip("\n"))
    lines.append("*ELEMENT, TYPE=S6, ELSET=EALL")
    lines += [f"{e + 1}, " + ", ".join(str(int(v) + 1) for v in conn[e]) for e in range(len(conn))]
    # materials
    for m in used_mats.values():
        lines.append(f"*MATERIAL, NAME={m.id}")
        if m.kind == MaterialKind.ISOTROPIC:
            lines += ["*ELASTIC", f"{_fmt(m.E)}, {_fmt(m.nu)}"]  # type: ignore[arg-type]
        else:
            c = m.engineering_constants()
            lines += ["*ELASTIC, TYPE=ENGINEERING CONSTANTS",
                      f"{_fmt(c['E1'])}, {_fmt(c['E2'])}, {_fmt(c['E3'])}, {_fmt(c['nu12'])}, {_fmt(c['nu13'])}, {_fmt(c['nu23'])}, "
                      f"{_fmt(c['G12'])}, {_fmt(c['G13'])},", f"{_fmt(c['G23'])}, 0."]
        lines += ["*DENSITY", _fmt(m.density)]
    # sections (laminates per normal-direction bin)
    dirs = fibonacci_directions()
    bins = np.argmax(normals @ dirs.T, axis=1)
    n_bins = 0
    for k, s in enumerate(st.sections):
        sel = np.flatnonzero(sec_of == k)
        if len(sel) == 0:
            continue
        if s.kind == SectionKind.SHEET:
            for suffix, members, off in _split_isolated(f"E_{s.id}", sel, plan.colour, plan.offsets[k]):
                lines.append(f"*ELSET, ELSET={suffix}")
                lines.append(_id_lines(members).rstrip("\n"))
                lines.append(f"*SHELL SECTION, ELSET={suffix}, MATERIAL={s.material}, OFFSET={off:g}")
                lines.append(_fmt(float(s.thickness)))  # type: ignore[arg-type]
            continue
        stack = list(reversed(s.stack()))  # CalculiX: from the negative-normal (inner) side
        z0 = np.asarray(s.zero_direction, dtype=float)
        z0 /= np.linalg.norm(z0)
        for b in np.unique(bins[sel]):
            members = sel[bins[sel] == b]
            nbar = normals[members].sum(0)
            nn = float(np.linalg.norm(nbar))
            nbar = nbar / nn if nn > 1e-12 else dirs[b]
            e0 = z0 - (z0 @ nbar) * nbar
            if np.linalg.norm(e0) < 0.25:  # 0° direction nearly normal to the surface here: use x, then z
                for alt in (np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])):
                    e0 = alt - (alt @ nbar) * nbar
                    if np.linalg.norm(e0) >= 0.25:
                        break
            e0 /= np.linalg.norm(e0)
            e90 = np.cross(nbar, e0)
            angles = sorted({p.angle for p in stack})
            onames = {}
            for a in angles:
                th = math.radians(a)
                ax = math.cos(th) * e0 + math.sin(th) * e90
                bx = np.cross(nbar, ax)
                on = f"O_{s.id}_{int(b)}_{len(onames)}"
                onames[a] = on
                lines.append(f"*ORIENTATION, NAME={on}")
                lines.append(", ".join(_fmt(v) for v in (*ax, *bx)))
            for name, part_members, off in _split_isolated(f"E_{s.id}_{int(b)}", members, plan.colour, plan.offsets[k]):
                lines.append(f"*ELSET, ELSET={name}")
                lines.append(_id_lines(part_members).rstrip("\n"))
                lines.append(f"*SHELL SECTION, ELSET={name}, COMPOSITE, OFFSET={off:g}")
                lines += [f"{_fmt(p.thickness)}, , {p.material}, {onames[p.angle]}" for p in stack]
            n_bins += 1
    info.orientation_bins = n_bins
    # supports
    patch_nodes, edge_nodes = [], []
    for sp in supports:
        ids = sup_sets[sp.id]
        info.support_counts[sp.id] = int(len(ids))
        inside = np.zeros(n_corner, dtype=bool)
        inside[ids[ids < n_corner]] = True
        whole = int(inside[mesh.tris].all(axis=1).sum())
        kind = "patch" if whole >= PATCH_MIN_ELEMENTS else "edge"
        info.support_kinds[sp.id] = kind
        (patch_nodes if kind == "patch" else edge_nodes).append(ids)
    info.reactions_exact = not edge_nodes
    lines.append("*NSET, NSET=NSUP")
    lines.append(_id_lines(all_sup).rstrip("\n"))
    if patch_nodes:
        ids = np.unique(np.concatenate(patch_nodes))
        lines += ["*NSET, NSET=NSUP_PATCH", _id_lines(ids).rstrip("\n"), "*BOUNDARY", "NSUP_PATCH, 1, 3"]
    if edge_nodes:
        ids = np.unique(np.concatenate(edge_nodes))
        lines += ["*NSET, NSET=NSUP_EDGE", _id_lines(ids).rstrip("\n"), "*BOUNDARY", "NSUP_EDGE, 1, 6"]
    # step
    lines += ["*STEP", "*STATIC"]
    loaded = np.flatnonzero(np.linalg.norm(F, axis=1) > 0)
    if len(loaded):
        lines.append("*CLOAD")
        for i in loaded:
            for d in range(3):
                if F[i, d] != 0.0:
                    lines.append(f"{i + 1}, {d + 1}, {_fmt(F[i, d])}")
    lines += ["*NODE PRINT, NSET=NCORNER", "U", "*NODE PRINT, NSET=NSUP, TOTALS=ONLY", "RF", "*EL PRINT, ELSET=EALL", "S",
              "*NODE FILE, OUTPUT=2D", "U", "*END STEP", ""]
    path.write_text("\n".join(lines), encoding="ascii")
    return info
