"""ParaView 6.2 sidecar integration on a real OpenFOAM result (no mocks)."""

import numpy as np
import pytest

from aerobench.services.viz import VizService
from aerobench.viz.payload import unpack

pytestmark = [pytest.mark.openfoam, pytest.mark.paraview, pytest.mark.slow]


@pytest.fixture(scope="module")
def viz(ctx):
    v = VizService(ctx)
    yield v
    v.close()


def test_presets_extract_valid_geometry(solved, viz):
    p, res = solved
    run_id = res["run_id"]
    presets = {pr["id"]: pr for pr in viz.presets(p, run_id)}
    for required in ("cp_body", "yplus_body", "centreline_cp", "rear_wake", "front_wheel_wake", "rear_wheel_wake",
                     "underfloor_velocity", "streamlines", "q_wake"):
        assert required in presets, required
    for pid in ("cp_body", "centreline_cp", "rear_wake", "underfloor_velocity", "streamlines", "yplus_body"):
        pr = presets[pid]
        out = viz.extract(p, run_id, pr["op"], pr["params"])
        hdr, arrays = unpack(viz.payload_path(p, run_id, out["key"]).read_bytes())
        n = hdr["n_points"]
        assert n > 50, pid
        assert len(arrays["positions"]) == 3 * n
        assert len(arrays["scalars"]) == n
        assert np.isfinite(arrays["scalars"]).mean() > 0.99
        if "indices" in arrays:
            assert arrays["indices"].max() < n
    # Cp on the body: stagnation near 1, suction below 0
    out = viz.extract(p, run_id, "surface", presets["cp_body"]["params"])
    lo, hi = out["header"]["scalar_range"]
    assert 0.8 < hi < 1.3 and lo < -0.3
    # second request is served from the cache
    assert viz.extract(p, run_id, "surface", presets["cp_body"]["params"])["cached"]


def test_q_isosurface_and_line_sample(solved, viz):
    p, res = solved
    out = viz.extract(p, res["run_id"], "isosurface", {"field": "Q", "values": [20.0], "color_field": "U_ratio"})
    assert out["header"]["n_points"] > 100
    ls = viz.line_sample(p, res["run_id"], p1=[-2, 0, 0.7], p2=[-0.05, 0, 0.7], resolution=40, fields=["p", "U"])
    u = [x for x in ls["U"] if x is not None]
    assert u[0] > u[-1]  # flow decelerates towards the stagnation point


def test_report_screenshot(solved, viz, tmp_path):
    p, res = solved
    png = viz.preset_screenshot(p, res["run_id"], "centreline_cp", out_dir=tmp_path, size=(800, 450))
    assert png.exists() and png.stat().st_size > 15000


def test_expert_script(solved, viz):
    p, res = solved
    s = viz.expert_script(p, res["run_id"], ["cp_body", "rear_wake"])
    assert "OpenFOAMReader" in s and "Slice" in s
    compile(s, "script", "exec")


def test_mesh_viewer_on_the_generated_mesh(solved, viz):
    """Mesh inspection after meshing: the real cell faces of the vehicle and ground, and a cut
    through the volume mesh coloured by refinement level."""
    p, res = solved
    from aerobench.services.runs import run_record

    mesh_id = run_record(p, res["run_id"])["mesh_revision_id"]
    view = viz.mesh_view(p, mesh_id)
    assert view["patches"]["vehicle"] and view["patches"]["ground"] == ["ground"]
    assert {"inlet", "outlet"} <= set(view["patches"]["domain"])
    plan = view["plan"]
    assert plan and plan["base_cell"] > 0

    out = viz.mesh_extract(p, mesh_id, "mesh_patches", {"patches": view["patches"]["vehicle"]})
    hdr, a = unpack(viz.payload_path(p, mesh_id, out["key"]).read_bytes())
    n = hdr["n_points"]
    assert hdr["n_faces"] > 100 and a["indices"].max() < n and a["edges"].max() < n
    # a closed polygonal surface has about twice as many edges as (mostly quad) faces
    assert 1.2 * hdr["n_faces"] < hdr["n_edges"] < 3.5 * hdr["n_faces"]
    # the snapped surface lies on the vehicle: its bounding box matches the posed geometry's
    pts = a["positions"].reshape(-1, 3)
    vb_lo, vb_hi = np.array(plan["vehicle_bbox_min"]), np.array(plan["vehicle_bbox_max"])
    assert np.all(pts.min(axis=0) > vb_lo - 0.1) and np.all(pts.max(axis=0) < vb_hi + 0.1)

    y0 = 0.5 * (plan["domain_min"][1] + plan["domain_max"][1])
    cut = viz.mesh_extract(p, mesh_id, "mesh_slice", {"origin": [2.0, y0, 0.5], "normal": [0, 1, 0]})
    hdr, a = unpack(viz.payload_path(p, mesh_id, cut["key"]).read_bytes())
    levels = set(np.unique(a["scalars"]).astype(int).tolist())
    # background cells and refined cells near the car both appear
    assert 0 in levels and max(levels) >= 3, levels
    assert hdr["n_cells"] > 200 and a["edges"].max() < hdr["n_points"]
    # all cut points lie in the plane
    assert np.allclose(a["positions"].reshape(-1, 3)[:, 1], y0, atol=1e-4)
    assert viz.mesh_extract(p, mesh_id, "mesh_slice", {"origin": [2.0, y0, 0.5], "normal": [0, 1, 0]})["cached"]
