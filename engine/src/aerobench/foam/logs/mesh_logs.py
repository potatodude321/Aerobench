"""Structured parsing of checkMesh and snappyHexMesh logs (raw output always retained)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional


def _num(s: str) -> float:
    return float(s)


@dataclass
class CheckMeshReport:
    points: Optional[int] = None
    faces: Optional[int] = None
    internal_faces: Optional[int] = None
    cells: Optional[int] = None
    boundary_patches: Optional[int] = None
    cell_types: dict[str, int] = field(default_factory=dict)
    patches: dict[str, dict] = field(default_factory=dict)
    bounding_box: Optional[tuple[list[float], list[float]]] = None
    total_volume: Optional[float] = None
    min_volume: Optional[float] = None
    max_volume: Optional[float] = None
    max_aspect_ratio: Optional[float] = None
    max_non_ortho: Optional[float] = None
    avg_non_ortho: Optional[float] = None
    severely_non_ortho_faces: int = 0
    max_skewness: Optional[float] = None
    highly_skew_faces: int = 0
    n_regions: Optional[int] = None
    failed_checks: int = 0
    failed_messages: list[str] = field(default_factory=list)
    mesh_ok: bool = False
    raw_tail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def parse_checkmesh(text: str) -> CheckMeshReport:
    r = CheckMeshReport()
    def grab(pat, conv=int):
        m = re.search(pat, text, re.M)
        return conv(m.group(1)) if m else None

    r.points = grab(r"^\s+points:\s+(\d+)")
    r.faces = grab(r"^\s+faces:\s+(\d+)")
    r.internal_faces = grab(r"^\s+internal faces:\s+(\d+)")
    r.cells = grab(r"^\s+cells:\s+(\d+)")
    r.boundary_patches = grab(r"^\s+boundary patches:\s+(\d+)")
    for kind in ("hexahedra", "prisms", "wedges", "pyramids", "tet wedges", "tetrahedra", "polyhedra"):
        v = grab(rf"^\s+{kind}:\s+(\d+)")
        if v is not None:
            r.cell_types[kind] = v
    m = re.search(r"Overall domain bounding box \(([^)]*)\) \(([^)]*)\)", text)
    if m:
        r.bounding_box = ([_num(x) for x in m.group(1).split()], [_num(x) for x in m.group(2).split()])
    m = re.search(r"Min volume = (\S+)\. Max volume = (\S+)\.\s+Total volume = (\S+)\.", text)
    if m:
        r.min_volume, r.max_volume, r.total_volume = (_num(m.group(i)) for i in (1, 2, 3))
    r.max_aspect_ratio = grab(r"Max aspect ratio = ([-+\d.eE]+)", _num)
    m = re.search(r"Mesh non-orthogonality Max: (\S+) average: (\S+)", text)
    if m:
        r.max_non_ortho, r.avg_non_ortho = _num(m.group(1)), _num(m.group(2))
    sev = grab(r"\*Number of severely non-orthogonal \(> \S+ degrees\) faces: (\d+)")
    r.severely_non_ortho_faces = sev or 0
    r.max_skewness = grab(r"Max skewness = ([-+\d.eE]+)", _num)
    hs = grab(r"(\d+) highly skew faces detected")
    r.highly_skew_faces = hs or 0
    r.n_regions = grab(r"Number of regions: (\d+)")
    fc = grab(r"Failed (\d+) mesh checks")
    r.failed_checks = fc or 0
    r.mesh_ok = "Mesh OK." in text and not fc
    r.failed_messages = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("***")]
    # patch table from "Checking patch topology"
    sec = re.search(r"Patch\s+Faces\s+Points\s+Surface topology\n(.*?)\n\n", text, re.S)
    if sec:
        for ln in sec.group(1).splitlines():
            parts = ln.split()
            if len(parts) >= 3 and parts[1].isdigit():
                r.patches[parts[0].strip('"')] = {"faces": int(parts[1]), "points": int(parts[2]), "topology": " ".join(parts[3:])}
    r.raw_tail = "\n".join(text.splitlines()[-40:])
    return r


@dataclass
class LayerCoverage:
    patch: str
    faces: int
    target_layers: float
    mesh_layers: float
    thickness: float
    thickness_pct: float

    @property
    def coverage(self) -> float:
        return self.mesh_layers / self.target_layers if self.target_layers else 0.0


@dataclass
class SnappyReport:
    castellated_cells: Optional[int] = None
    snapped_cells: Optional[int] = None
    layer_cells: Optional[int] = None
    final_cells: Optional[int] = None
    layers: list[LayerCoverage] = field(default_factory=list)
    finished_ok: Optional[bool] = None
    meshing_time: Optional[float] = None
    displacement_warnings: int = 0
    cells_per_level: dict[int, int] = field(default_factory=dict)
    errors_in_final_mesh: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["layers"] = [dict(asdict(l), coverage=l.coverage) for l in self.layers]
        d["overall_layer_coverage"] = self.overall_layer_coverage()
        return d

    def overall_layer_coverage(self) -> Optional[float]:
        tot = sum(l.faces * l.target_layers for l in self.layers)
        if not tot:
            return None
        return sum(l.faces * l.mesh_layers for l in self.layers) / tot


def parse_snappy(text: str) -> SnappyReport:
    r = SnappyReport()
    def cells(pat):
        ms = re.findall(pat, text)
        return int(ms[-1]) if ms else None

    r.castellated_cells = cells(r"Refined mesh : cells:(\d+)") or cells(r"Castellated mesh : cells:(\d+)")
    r.snapped_cells = cells(r"Snapped mesh : cells:(\d+)")
    r.layer_cells = cells(r"Layer mesh : cells:(\d+)")
    r.final_cells = r.layer_cells or r.snapped_cells or r.castellated_cells
    if "Finished meshing without any errors" in text:
        r.finished_ok = True
    elif "Finished meshing with" in text:
        r.finished_ok = False
    m = re.findall(r"Finished meshing in = ([\d.]+) s", text)
    if m:
        r.meshing_time = float(m[-1])
    r.displacement_warnings = text.count("points through the surrounding patch faces")
    tbl = re.findall(r"patch\s+faces\s+layers\s+overall thickness\n.*?\n-+.*?\n(.*?)\n\n", text, re.S)
    if tbl:
        for ln in tbl[-1].splitlines():
            p = ln.split()
            if len(p) >= 6:
                try:
                    r.layers.append(LayerCoverage(p[0], int(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
                except ValueError:
                    continue
    lv = re.findall(r"Cells per refinement level:\n((?:\s+\d+\s+\d+\n)+)", text)
    if lv:
        for ln in lv[-1].strip().splitlines():
            a, b = ln.split()
            r.cells_per_level[int(a)] = int(b)
    final = text.split("Checking final mesh")[-1] if "Checking final mesh" in text else ""
    for ln in final.splitlines():
        m = re.match(r"\s+(.+?)\s+:\s+(\d+)$", ln)
        if m:
            r.errors_in_final_mesh[m.group(1).strip()] = int(m.group(2))
    return r
