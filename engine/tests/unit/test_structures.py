"""Structural check of aero elements: materials, load mapping, deck, CalculiX results."""

import math
import subprocess

import numpy as np
import pytest

from aerobench.core.errors import AeroBenchError
from aerobench.domain.structure import Material, StructureConfig
from aerobench.structures.ccx import detect_calculix
from aerobench.structures.deck import write_deck
from aerobench.structures.expansion import choose_offsets
from aerobench.structures.loads import map_loads, s6_shape, subdivision_points
from aerobench.structures.materials import BUILTIN, all_materials, load_user_library
from aerobench.structures.mesh import StructMesh
from aerobench.structures.refine import improve
from aerobench.structures.results import MODES, assess, max_stress_rf, parse_dat, summarise, tsai_wu_rf

CCX = detect_calculix()
needs_ccx = pytest.mark.skipif(not CCX["available"], reason="CalculiX (ccx) is not installed")


# ---------------------------------------------------------------------------- helpers
def plate(nx: int, ny: int, lx: float = 1.0, ly: float = 0.2) -> StructMesh:
    """Flat rectangle in z = 0, normals +z."""
    xs, ys = np.linspace(0, lx, nx + 1), np.linspace(0, ly, ny + 1)
    P = np.array([[x, y, 0.0] for y in ys for x in xs])
    idx = lambda i, j: j * (nx + 1) + i  # noqa: E731
    T = []
    for j in range(ny):
        for i in range(nx):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            T += [(a, b, c), (a, c, d)]
    T = np.array(T)
    return StructMesh(points=P, tris=T, part=np.zeros(len(T), dtype=np.int64), part_ids=["plate"])


def tube(r: float = 0.005, length: float = 0.05, nc: int = 24, nl: int = 10, x0: float = 0.0):
    th = np.linspace(0, 2 * np.pi, nc, endpoint=False)
    z = np.linspace(0, length, nl + 1)
    P = np.array([[x0 + r * np.cos(t), r * np.sin(t), zz] for zz in z for t in th])
    F = []
    for j in range(nl):
        for i in range(nc):
            a, b = j * nc + i, j * nc + (i + 1) % nc
            F += [(a, b, b + nc), (a, b + nc, a + nc)]
    return P, np.array(F)


def cfd_faces(nx: int, ny: int, lx: float, ly: float, p_of_x, wss=(0.0, 0.0, 0.0)) -> dict:
    """Flow faces on top of the plate: area vectors point out of the fluid, into the plate (-z)."""
    m = plate(nx, ny, lx, ly)
    t = m.points[m.tris]
    c = t.mean(axis=1)
    S = -0.5 * np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return {"plate/centre": c, "plate/area": S, "plate/p": p_of_x(c[:, 0]), "plate/wss": np.tile(np.asarray(wss, float), (len(c), 1))}


def sheet_config(material: str, thickness: float, supports, model: str = "skin") -> StructureConfig:
    return StructureConfig.model_validate({
        "model": model, "self_weight": False,
        "sections": [{"id": "s", "name": "Sheet", "kind": "sheet", "material": material, "thickness": thickness}],
        "supports": supports})


def run_ccx(tmp_path, mesh, st, nodal):
    info = write_deck(tmp_path / "model.inp", mesh, st, all_materials([]), nodal, 1.0)
    out = subprocess.run([CCX["ccx"], "-i", "model"], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert "Job finished" in out.stdout, out.stdout[-3000:]
    return info, parse_dat(tmp_path / "model.dat")


def tip_load(mesh: StructMesh, total: float, lx: float = 1.0) -> np.ndarray:
    nodes, _ = mesh.s6()
    F = np.zeros((len(nodes), 3))
    tip = np.flatnonzero(nodes[:, 0] > lx - 1e-9)
    F[tip, 2] = total / len(tip)
    return F


# ---------------------------------------------------------------------------- materials
def test_builtin_materials_are_complete_and_physical():
    ids = [m.id for m in BUILTIN]
    assert len(ids) == len(set(ids)) and len(ids) >= 15
    for m in BUILTIN:
        assert m.builtin and m.density > 0 and m.source
        if m.kind.value == "lamina":
            c = m.engineering_constants()
            assert all(c[k] > 0 for k in ("E1", "E2", "E3", "G12", "G13", "G23"))
            assert m.ply_thickness and m.Xc and m.Yc


def test_impossible_lamina_is_rejected():
    with pytest.raises(ValueError, match="not physically possible"):
        Material(id="bad", name="Bad", kind="lamina", density=1500, E1=10e9, E2=100e9, G12=5e9, nu12=0.9,
                 Xt=1e9, Xc=1e9, Yt=1e8, Yc=1e8, S12=1e8)
    with pytest.raises(ValueError, match="missing"):
        Material(id="half", name="Half", kind="lamina", density=1500, E1=100e9)


def test_user_material_library_round_trip(tmp_path, monkeypatch):
    from aerobench.services import structures as svc

    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    assert load_user_library() == []
    m = svc.save_user_material({"id": "my_prepreg", "name": "My prepreg", "kind": "lamina", "density": 1550, "E1": 125e9, "E2": 8.5e9,
                                "G12": 4.5e9, "nu12": 0.3, "Xt": 2.0e9, "Xc": 1.2e9, "Yt": 50e6, "Yc": 200e6, "S12": 80e6,
                                "ply_thickness": 0.0002, "source": "supplier sheet"})
    assert m["builtin"] is False
    assert [x.id for x in load_user_library()] == ["my_prepreg"]
    assert "my_prepreg" in all_materials([])
    with pytest.raises(AeroBenchError):
        svc.save_user_material({**m, "id": "al_6061_t6"})
    svc.delete_user_material("my_prepreg")
    assert load_user_library() == []


# ---------------------------------------------------------------------------- loads
def test_subdivision_and_shape_functions():
    for n in range(1, 7):
        B = subdivision_points(n)
        assert len(B) == n * n
        assert np.allclose(B.sum(1), 1.0) and np.all(B > 0)
        assert np.allclose(B.mean(0), 1 / 3)  # equal-area sub-triangles: their centroids average to the centroid
        N = s6_shape(B)
        assert np.allclose(N.sum(1), 1.0)
    # the shape functions interpolate: 1 at their own node, 0 at the others
    nodes = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5]], float)
    assert np.allclose(s6_shape(nodes), np.eye(6))


@pytest.mark.parametrize("structure, flow", [((8, 4), (40, 20)), ((40, 20), (6, 3))])
def test_load_mapping_keeps_force_and_moment(structure, flow):
    mesh = plate(*structure, lx=1.0, ly=0.5)
    data = cfd_faces(*flow, 1.0, 0.5, lambda x: 100.0 + 400.0 * x, wss=(2.0, 0.0, 0.0))
    rho = 1.2
    ml = map_loads(mesh, data, rho=rho, p_ref=0.0)
    S = data["plate/area"]
    f = rho * data["plate/p"][:, None] * S - rho * data["plate/wss"] * np.linalg.norm(S, axis=1)[:, None]
    assert np.allclose(ml.total, f.sum(0))
    assert np.allclose(ml.nodal.sum(0), f.sum(0), rtol=1e-9)  # exact: the remainder is spread over the part
    nodes, _ = mesh.s6()
    m_cfd = np.cross(data["plate/centre"], f).sum(0)
    m_map = np.cross(nodes, ml.nodal).sum(0)
    assert np.allclose(m_map, m_cfd, rtol=0.02, atol=0.02 * np.abs(m_cfd).max())
    assert ml.correction < 0.02
    # pressure presses the plate down (into it), friction drags it downstream
    assert ml.total[2] < 0 and ml.total[0] < 0


def test_loads_of_unknown_part_are_reported():
    mesh = plate(4, 2)
    ml = map_loads(mesh, {}, rho=1.2, p_ref=0.0)
    assert not ml.nodal.any() and "No flow loads" in ml.notes[0]


# ---------------------------------------------------------------------------- mesh
def test_refine_keeps_shape_orientation_and_parts():
    P1, F1 = tube(r=0.02, length=0.5, nc=24, nl=1)  # long slivers
    P2, F2 = tube(r=0.02, length=0.5, nc=24, nl=1, x0=0.2)
    P = np.vstack([P1, P2])
    F = np.vstack([F1, F2 + len(P1)])
    R = np.r_[np.zeros(len(F1), int), np.ones(len(F2), int)]

    def area(P, F):
        t = P[F]
        return 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)

    target = 0.02
    P_, F_, R_, info = improve(P, F, R, target, max_faces=20000)
    assert info["faces_out"] > 4 * len(F)
    assert area(P_, F_).sum() == pytest.approx(area(P, F).sum(), rel=1e-9)
    for r, x0 in ((0, 0.0), (1, 0.2)):
        assert area(P_, F_[R_ == r]).sum() == pytest.approx(area(P, F[R == r]).sum(), rel=1e-9)
        # every vertex of a part stays on that part's tube (no mixing, no moving off the facets)
        v = P_[np.unique(F_[R_ == r])]
        rad = np.hypot(v[:, 0] - x0, v[:, 1])
        assert np.all(rad <= 0.02 + 1e-12) and np.all(rad >= 0.02 * math.cos(math.pi / 24) - 1e-12)
    t = P_[F_]
    e = np.linalg.norm(t - np.roll(t, -1, axis=1), axis=2)
    assert e.max() <= 1.334 * target
    # orientation unchanged: normals still point away from each tube's axis
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    c = t.mean(1)
    axis_x = np.where(R_ == 0, 0.0, 0.2)
    radial = np.stack([c[:, 0] - axis_x, c[:, 1], np.zeros(len(c))], 1)
    assert np.all((n * radial).sum(1) > 0)


def test_refine_respects_the_element_budget():
    P, F = tube(r=0.02, length=0.5, nc=24, nl=1)
    _, F_, _, info = improve(P, F, np.zeros(len(F), int), 0.002, max_faces=3000)
    assert len(F_) <= 3000 and info["faces_out"] == len(F_)


# ---------------------------------------------------------------------------- shell expansion
def test_section_offset_follows_what_fits_the_curvature():
    P, F = tube(r=0.005)
    m = StructMesh(points=P, tris=F, part=np.zeros(len(F), int), part_ids=["t"])
    X, C = m.s6()
    sec = np.zeros(len(F), int)
    assert choose_offsets(X, C, m.normals(), sec, [0.3 * 0.005]).offsets == [0.5]  # fits inside
    ch = choose_offsets(X, C, m.normals(), sec, [0.95 * 0.005])
    assert ch.offsets == [0.0] and ch.centred == [0] and not ch.failing  # only fits centred
    assert choose_offsets(X, C, m.normals(), sec, [2.6 * 0.005]).failing  # does not fit at all


def test_too_thick_section_is_refused_before_solving(tmp_path):
    P, F = tube(r=0.005)
    m = StructMesh(points=P, tris=F, part=np.zeros(len(F), int), part_ids=["t"])
    st = sheet_config("al_6061_t6", 0.013, [{"id": "a", "name": "A", "shape": {"kind": "box", "min": [-1, -1, -0.001], "max": [1, 1, 0.006]}}])
    nodes, _ = m.s6()
    with pytest.raises(AeroBenchError) as e:
        write_deck(tmp_path / "m.inp", m, st, all_materials([]), np.zeros((len(nodes), 3)), 1.0)
    assert e.value.diagnostic.code == "structure.shell_too_thick"


# ---------------------------------------------------------------------------- deck
def test_supports_patch_or_edge_and_held_loads(tmp_path):
    mesh = plate(20, 4)
    nodes, _ = mesh.s6()
    F = np.full((len(nodes), 3), [0.0, 0.0, -1.0])
    patch = {"id": "root", "name": "Root", "shape": {"kind": "box", "min": [-0.01, -0.01, -0.01], "max": [0.11, 0.21, 0.01]}}
    edge = {"id": "line", "name": "Line", "shape": {"kind": "box", "min": [0.99, -0.01, -0.01], "max": [1.01, 0.21, 0.01]}}
    info = write_deck(tmp_path / "a.inp", mesh, sheet_config("al_6061_t6", 0.003, [patch]), all_materials([]), F, 2.0)
    assert info.support_kinds == {"root": "patch"} and info.reactions_exact
    held_nodes = np.flatnonzero(nodes[:, 0] <= 0.11)
    assert info.held_by_supports == pytest.approx([0.0, 0.0, -2.0 * len(held_nodes)])
    assert info.applied_total == pytest.approx([0.0, 0.0, -2.0 * len(nodes)])
    text = (tmp_path / "a.inp").read_text()
    assert "NSUP_PATCH, 1, 3" in text and "NSUP_EDGE" not in text
    cload = text.split("*CLOAD\n")[1].split("*")[0].strip().splitlines()
    assert {int(ln.split(",")[0]) - 1 for ln in cload}.isdisjoint(held_nodes)
    info = write_deck(tmp_path / "b.inp", mesh, sheet_config("al_6061_t6", 0.003, [patch, edge]), all_materials([]), F, 1.0)
    assert info.support_kinds == {"root": "patch", "line": "edge"} and not info.reactions_exact
    assert "NSUP_EDGE, 1, 6" in (tmp_path / "b.inp").read_text()


def test_self_weight_is_written_as_consistent_nodal_loads(tmp_path):
    mesh = plate(10, 2)
    nodes, _ = mesh.s6()
    st = sheet_config("steel_s355", 0.002, [{"id": "a", "name": "A", "shape": {"kind": "box", "min": [-1, -1, -1], "max": [0.05, 1, 1]}}])
    st = st.model_copy(update={"self_weight": True})
    info = write_deck(tmp_path / "w.inp", mesh, st, all_materials([]), np.zeros((len(nodes), 3)), 1.0)
    mass = 1.0 * 0.2 * 0.002 * 7850
    assert info.mass == pytest.approx(mass)
    text = (tmp_path / "w.inp").read_text()
    assert "GRAV" not in text
    rows = [ln.split(",") for ln in text.split("*CLOAD\n")[1].split("*")[0].strip().splitlines()]
    written = sum(float(r[2]) for r in rows)
    assert written + info.held_by_supports[2] == pytest.approx(-mass * 9.81)


def test_laminate_plies_are_written_inner_first_with_orientations(tmp_path):
    mesh = plate(4, 2)
    nodes, _ = mesh.s6()
    st = StructureConfig.model_validate({"self_weight": False, "supports": [{"id": "a", "name": "A", "shape": {"kind": "box", "min": [-1, -1, -1], "max": [0.3, 1, 1]}}],
                                         "sections": [{"id": "lam", "name": "Lam", "kind": "laminate", "zero_direction": [1, 0, 0], "plies": [
                                             {"material": "cfrp_twill_t300", "thickness": 0.0002, "angle": 45},
                                             {"material": "cfrp_ud_t700", "thickness": 0.00015, "angle": 0}]}]})
    info = write_deck(tmp_path / "l.inp", mesh, st, all_materials([]), np.zeros((len(nodes), 3)), 1.0)
    assert info.element_layers == [["cfrp_ud_t700", "cfrp_twill_t300"]]  # inner (UD) first
    text = (tmp_path / "l.inp").read_text()
    sec = text.split("COMPOSITE, OFFSET=0.5\n")[1].splitlines()[:2]
    assert sec[0].startswith("0.00015, , cfrp_ud_t700") and sec[1].startswith("0.0002, , cfrp_twill_t300")
    # the 0 degree orientation of a flat plate in xy with 0 deg along x is the x axis
    o = text.split("*ORIENTATION, NAME=")[1:]
    axes = {}
    for block in o:
        name, row = block.splitlines()[:2]
        axes[name] = np.array([float(v) for v in row.split(",")])
    zero = [a for n, a in axes.items() if sec[0].endswith(n)][0]
    assert np.allclose(zero[:3], [1, 0, 0], atol=1e-9)
    ply45 = [a for n, a in axes.items() if sec[1].endswith(n)][0]
    assert np.allclose(ply45[:3], [math.sqrt(0.5), math.sqrt(0.5), 0], atol=1e-9)


# ---------------------------------------------------------------------------- failure criteria
def test_failure_criteria_reach_one_at_the_strengths():
    m = {x.id: x for x in BUILTIN}["cfrp_ud_t700"]
    z = np.zeros(1)
    for s1, s2, t12 in ((m.Xt, 0, 0), (-m.Xc, 0, 0), (0, m.Yt, 0), (0, -m.Yc, 0), (0, 0, m.S12)):
        assert tsai_wu_rf(m, np.array([s1]) + z, np.array([s2]) + z, np.array([t12]) + z)[0] == pytest.approx(1.0, rel=1e-9)
        assert max_stress_rf(m, np.array([s1]) + z, np.array([s2]) + z, np.array([t12]) + z)[0][0] == pytest.approx(1.0)
    rf, mode = max_stress_rf(m, np.array([-m.Xc / 4]), z, z)
    assert rf[0] == pytest.approx(4.0) and MODES[mode[0]] == "fibre compression"


def test_assessment_names_the_ply_from_the_outside():
    mats = {x.id: x for x in BUILTIN}
    # one laminate element with three plies (CalculiX: 6 integration points per layer, inner layer first)
    rows = []
    for ip in range(18):
        s = [0.0] * 6
        if ip // 6 == 0:  # innermost layer: fibre tension at half the strength
            s[0] = mats["cfrp_ud_t700"].Xt / 2
        rows.append([1, ip + 1, *s])
    a = assess(np.array(rows, float), 1, [0], [["cfrp_ud_t700"] * 3], ["laminate"], mats)
    assert a.element_rf[0] == pytest.approx(2.0, rel=1e-6)
    assert a.element_layer[0] == 3 and MODES[a.element_mode[0]] == "fibre tension"
    s = summarise(a, np.zeros((3, 3)), np.zeros((1, 3)), 1.5, 1.0, np.array([0.0, 0.0, 10.0]), np.array([0.0, 0.0, -9.0]),
                  np.array([0.0, 0.0, -1.0]), [0], ["Lam"], held=np.zeros(3))
    assert s["passed"] and s["equilibrium_error"] == pytest.approx(0.0) and s["critical"]["ply"] == 3


# ---------------------------------------------------------------------------- CalculiX
@needs_ccx
def test_cantilever_sheet_matches_beam_theory(tmp_path):
    mesh = plate(40, 4, 1.0, 0.2)
    t, P = 0.01, 100.0
    st = sheet_config("al_6061_t6", t, [{"id": "root", "name": "Root", "shape": {"kind": "box", "min": [-0.01, -0.01, -0.01], "max": [0.0501, 0.21, 0.01]}}])
    info, res = run_ccx(tmp_path, mesh, st, tip_load(mesh, P))
    assert info.support_kinds == {"root": "patch"}
    U = res["U:NCORNER"]
    tip = U[mesh.points[U[:, 0].astype(int) - 1, 0] > 1 - 1e-9, 3].mean()
    E, L, I = 68.9e9, 0.95, 0.2 * t ** 3 / 12
    assert tip == pytest.approx(P * L ** 3 / (3 * E * I), rel=0.08)
    R = res["RF:NSUP"]
    assert R == pytest.approx([0.0, 0.0, -P], abs=1e-6 * P)


@needs_ccx
def test_unidirectional_laminate_cantilever_fails_in_the_outer_ply_in_compression(tmp_path):
    mesh = plate(40, 4, 1.0, 0.2)
    mats = all_materials([])
    ud = mats["cfrp_ud_t700"]
    n, tp, P = 8, 0.00015, 20.0
    st = StructureConfig.model_validate({"self_weight": False, "supports": [{"id": "root", "name": "Root", "shape": {"kind": "box", "min": [-0.01, -0.01, -0.01], "max": [0.0501, 0.21, 0.01]}}],
                                         "sections": [{"id": "ud", "name": "UD", "kind": "laminate", "zero_direction": [1, 0, 0],
                                                       "plies": [{"material": "cfrp_ud_t700", "thickness": tp, "angle": 0}] * n}]})
    info, res = run_ccx(tmp_path, mesh, st, tip_load(mesh, P))
    U = res["U:NCORNER"]
    tip = U[mesh.points[U[:, 0].astype(int) - 1, 0] > 1 - 1e-9, 3].mean()
    t = n * tp
    assert tip == pytest.approx(P * 0.95 ** 3 / (3 * ud.E1 * 0.2 * t ** 3 / 12), rel=0.1)
    a = assess(res["S:EALL"], mesh.n_tris, info.element_section, info.element_layers, info.section_kinds, mats)
    crit = int(np.argmin(a.element_rf))
    # tip pushed up (+z, the outer side): the outer ply is compressed at the root, the inner one stretched
    assert a.element_layer[crit] == 1 and MODES[a.element_mode[crit]] == "fibre compression"
    sigma = P * 0.95 * (t / 2) / (0.2 * t ** 3 / 12)  # beam root stress at the support edge
    assert a.element_rf[crit] == pytest.approx(ud.Xc / sigma, rel=0.25)


# ---------------------------------------------------------------------------- aero element setup
@pytest.fixture()
def element_project(tmp_path, monkeypatch):
    from aerobench.domain.project import ProjectConfig
    from aerobench.domain.templates import apply_template
    from aerobench.geometry.fixtures import wing
    from aerobench.geometry.io import write_stl
    from aerobench.project.store import Project
    from aerobench.services import geometry as geo

    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    write_stl(tmp_path / "wing.stl", wing(0.3, 1.2, (0.0, 0.0, 0.5), angle_deg=0.0))
    p = Project.create(tmp_path / "p", "p", apply_template(ProjectConfig(), "aero_element"))
    geo.import_geometry(p, tmp_path / "wing.stl")
    return p


def _clearance(p):
    """Height of the element's lowest point above the ground, as posed in the flow case."""
    from aerobench.geometry.pose import posed_components

    c = p.config
    parts, ground, _ = posed_components(p.cfd_surface(cfg=c), c.geometry.components, c.vehicle, c.tunnel.contact_patch_fraction)
    return min(float(s.bbox()[0][2]) for s in parts.values() if s.n_faces) - ground


def test_element_setup_places_the_element_over_the_ground(element_project):
    from aerobench.services import element as el

    p = element_project
    assert p.config.application == "aero_element" and not p.config.vehicle.wheels
    ref = p.config.reference
    assert ref.area_planform_computed == pytest.approx(0.3 * 1.2, rel=0.03)  # the outline seen from above
    out = el.apply_element_setup(p, environment="ground_effect", ground_clearance=0.08, angle_deg=0.0)
    assert out["clearance"] == pytest.approx(0.08)
    assert _clearance(p) == pytest.approx(0.08, abs=1e-6)
    el.apply_element_setup(p, angle_deg=10.0)  # tilting keeps the requested clearance
    assert p.config.vehicle.rake_deg == pytest.approx(10.0)
    assert _clearance(p) == pytest.approx(0.08, abs=1e-6)
    out = el.apply_element_setup(p, environment="free_stream", free_stream_chords=4.0)
    assert out["clearance"] == pytest.approx(4.0 * out["chord"])
    assert _clearance(p) == pytest.approx(4.0 * out["chord"], abs=1e-6)
