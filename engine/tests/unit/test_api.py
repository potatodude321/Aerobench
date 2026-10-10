
import pytest
from fastapi.testclient import TestClient

from aerobench.api.app import create_app
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.services.context import AppContext
from aerobench.settings import AppSettings

TOKEN = "test-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    app = create_app(AppContext(AppSettings()), token=TOKEN)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_health_is_public_and_api_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path / "cfg"))
    app = create_app(AppContext(AppSettings()), token=TOKEN)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/templates").status_code == 401
        assert c.get("/api/templates", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/api/templates", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def _project(client, tmp_path, template="generic_road_car"):
    r = client.post("/api/projects", json={"path": str(tmp_path / "P"), "name": "API test", "template_id": template})
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    stl = tmp_path / "car.stl"
    write_stl(stl, generic_car())
    r = client.post(f"/api/p/{pid}/geometry/import", json={"path": str(stl)})
    assert r.status_code == 200, r.text
    return pid


def test_project_geometry_config_flow(client, tmp_path):
    pid = _project(client, tmp_path)
    g = client.get(f"/api/p/{pid}/geometry").json()
    assert len(g["wheels"]) == 4 and g["summary"]["triangle_count"] > 1000
    assert g["reference"]["area"] == pytest.approx(g["reference"]["area_computed"])
    st = client.get(f"/api/p/{pid}").json()
    assert st["config"]["template_id"] == "generic_road_car"
    assert not [v for v in st["validation"] if v["severity"] == "error"]
    # patch config
    r = client.patch(f"/api/p/{pid}/config", json={"operating": {"speed": 55.0, "yaw_deg": 5.0}})
    assert r.status_code == 200 and r.json()["config"]["operating"]["speed"] == 55.0
    # invalid values are rejected with a structured error
    r = client.patch(f"/api/p/{pid}/config", json={"operating": {"speed": -1}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "config.validation"
    # half-car + yaw is a blocking validation error
    r = client.patch(f"/api/p/{pid}/config", json={"tunnel": {"symmetry": "half"}})
    errs = [v for v in r.json()["validation"] if v["severity"] == "error"]
    assert any(v["code"] == "tunnel.symmetry_yaw" for v in errs)
    client.patch(f"/api/p/{pid}/config", json={"tunnel": {"symmetry": "full"}})
    d = client.get(f"/api/p/{pid}/derived").json()
    assert d["reynolds_length"] > 1e6 and d["wheels"][0]["omega_rad_s"] > 0
    assert d["freestream_vector"][1] < 0  # yaw +5: wind from the right


def test_mesh_plan_and_expert_preview(client, tmp_path):
    pid = _project(client, tmp_path)
    plan = client.get(f"/api/p/{pid}/mesh/plan").json()
    assert plan["plan"]["estimate_cells"] > 100000
    assert {r["kind"] for r in plan["plan"]["regions"]} >= {"wake_near", "wake_far", "underbody", "wheel"}
    prev = client.get(f"/api/p/{pid}/expert/preview/solve").json()
    assert "system/controlDict" in prev["files"] and "0/U" in prev["files"]
    assert "rotatingWallVelocity" in prev["files"]["0/U"]
    prev_m = client.get(f"/api/p/{pid}/expert/preview/mesh").json()
    assert "snappyHexMeshDict" in "".join(prev_m["files"])
    # expert override survives regeneration and is reported
    r = client.patch(f"/api/p/{pid}/config", json={"expert": {"patches": [
        {"file": "system/fvSolution", "key_path": "SIMPLE/nNonOrthogonalCorrectors", "value": 2, "stage": "solve"},
        {"file": "system/fvSolution", "key_path": "nope/missing", "op": "delete", "stage": "solve"}]}})
    assert r.status_code == 200
    prev = client.get(f"/api/p/{pid}/expert/preview/solve").json()
    assert "nNonOrthogonalCorrectors 2;" in prev["files"]["system/fvSolution"]
    assert prev["overrides"]["applied"] and prev["overrides"]["conflicts"]


def test_preview_payload_and_templates(client, tmp_path):
    from aerobench.viz.payload import unpack

    pid = _project(client, tmp_path)
    r = client.get(f"/api/p/{pid}/geometry/preview.abg")
    hdr, arr = unpack(r.content)
    assert hdr["part_names"][0] == "body" and len(arr["indices"]) % 3 == 0
    t = client.get("/api/templates").json()
    assert {x["id"] for x in t} >= {"generic_road_car", "formula_student", "half_car", "full_car_yaw"}
    r = client.post(f"/api/p/{pid}/presets/template/full_car_yaw")
    assert r.json()["config"]["tunnel"]["design_yaw_range_deg"] == [-10.0, 10.0]


def test_reference_lock_requires_confirmation(client, tmp_path):
    pid = _project(client, tmp_path)
    client.patch(f"/api/p/{pid}/config", json={"reference": {"locked": True}})
    r = client.patch(f"/api/p/{pid}/config", json={"reference": {"area": 2.5}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "reference.locked"
    r = client.patch(f"/api/p/{pid}/config", json={"reference": {"area": 2.5, "area_source": "manual", "confirm_after_results": True}})
    assert r.status_code == 200 and r.json()["config"]["reference"]["area"] == 2.5


def test_exploration_endpoints(client, tmp_path):
    pid = _project(client, tmp_path)
    t = client.get(f"/api/p/{pid}/studies/targets")
    assert t.status_code == 200 and any(x["target"] == "operating.yaw_deg" for x in t.json())
    defn = {"id": "y", "name": "Yaw", "kind": "one_factor", "variables": [{"id": "yaw", "target": "operating.yaw_deg", "lower": -5, "upper": 5}],
            "levels": {"yaw": [-5, 0, 5]}}
    pr = client.post(f"/api/p/{pid}/studies/preview", json={"kind": "study", "definition": defn}).json()
    assert not pr["errors"] and len(pr["designs"]) == 3 and pr["notes"]
    bad = client.post(f"/api/p/{pid}/studies/preview", json={"kind": "optimisation", "definition": {
        "id": "o", "name": "O", "algorithm": "gp_bo", "variables": defn["variables"],
        "objectives": [{"metric": "Cd"}, {"metric": "Cl"}]}})
    assert bad.status_code == 422  # multi-objective GP-BO is rejected by the model
    assert client.get(f"/api/p/{pid}/studies").json() == []
    # geometry parameters round trip + preview payload with validity check
    params = [{"id": "nose", "name": "Nose", "lower": -0.03, "upper": 0.03, "baseline": 0.0, "components": ["body"],
               "deformation": {"kind": "rbf", "handles": [[[0.0, 0.0, 0.5], [-1.0, 0.0, 0.0]]], "support_radius": 0.6}}]
    r = client.put(f"/api/p/{pid}/parameters", json={"parameters": params})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/p/{pid}/parameters").json()["parameters"][0]["id"] == "nose"
    b = client.post(f"/api/p/{pid}/parameters/preview.abg", json={"values": {"nose": 0.02}})
    assert b.status_code == 200 and b.content[:4] == b"ABG1"
    from aerobench.viz.payload import unpack

    header, arrays = unpack(b.content)
    assert header["check"]["valid"] is True and 0.019 < header["check"]["stats"]["max_displacement"] < 0.021
    assert arrays["scalars"].max() == pytest.approx(20.0, rel=0.05)  # mm
    r = client.put(f"/api/p/{pid}/parameters", json={"parameters": [{**params[0], "components": ["nope"]}]})
    assert r.status_code == 400
    assert client.get(f"/api/p/{pid}/reports").json() == []


def test_path_traversal_is_rejected(client, tmp_path):
    pid = _project(client, tmp_path)
    for bad in (f"/api/p/{pid}/runs/..%5C..%5Csecret/viz/image/a.png", f"/api/p/{pid}/runs/x/viz/image/..%2F..%2Fproject.json",
                f"/api/p/{pid}/jobs/..%5Cx/log"):
        r = client.get(bad)
        assert r.status_code in (400, 404), (bad, r.status_code)
        assert "FoamFile" not in r.text and "project_id" not in r.text
    r = client.get(f"/api/p/{pid}/runs/run-1/viz/image/evil.txt")
    assert r.status_code == 400


def test_new_projects_default_to_the_machine_cores(client, tmp_path, monkeypatch):
    from aerobench.execution import registry
    from aerobench.services import context

    monkeypatch.setattr(context, "usable_cores", lambda: 6)
    monkeypatch.setattr(registry, "usable_cores", lambda: 6)
    r = client.post("/api/projects", json={"path": str(tmp_path / "Cores"), "name": "Cores"})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["execution"]["n_procs"] == 6
    # Linux cannot run a path with spaces in parallel: such a project starts serial instead
    # of failing on its first run
    r = client.post("/api/projects", json={"path": str(tmp_path / "With space" / "P"), "name": "Spaced"})
    expected = 1 if client.app.state.engine.ctx.provider().kind == "local-posix" else 6
    assert r.json()["config"]["execution"]["n_procs"] == expected


def test_wing_demo_and_structure_routes(client, tmp_path):
    r = client.post("/api/projects/demo", json={"kind": "wing", "parent": str(tmp_path / "demos")})
    assert r.status_code == 200, r.text
    d = r.json()
    pid = d["project_id"]
    cfg = d["config"]
    assert cfg["application"] == "aero_element" and "wing" in cfg["tags"] and d["structure"] == {"last": None}
    assert cfg["reference"]["area_source"] == "computed_planform"
    # the element sits 60 mm over the ground at 5 degrees
    r = client.patch(f"/api/p/{pid}/element", json={"ground_clearance": 0.05})
    assert r.status_code == 200 and r.json()["clearance"] == pytest.approx(0.05)
    s = client.get(f"/api/p/{pid}/structure").json()
    assert s["runs"] == [] and s["analysed"] == ["front_wing"] and s["structure"]["sections"] and len(s["structure"]["supports"]) == 2
    assert {m["id"] for m in s["materials"]["builtin"]} >= {"cfrp_ud_t700", "al_6061_t6"}
    pre = client.get(f"/api/p/{pid}/structure/presets").json()
    assert {p["preset"] for p in pre["supports"]} == {"ends", "pylons", "centre", "rear_edge"}
    # a structural check needs the loads of a finished flow simulation
    r = client.post(f"/api/p/{pid}/structure/analyses", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] in ("structure.no_run", "structure.no_ccx")
    # settings round trip, invalid ones refused
    st = s["structure"] | {"required_reserve": 2.0}
    assert client.put(f"/api/p/{pid}/structure", json={"structure": st}).json()["required_reserve"] == 2.0
    bad = st | {"sections": [st["sections"][0] | {"plies": []}]}
    assert client.put(f"/api/p/{pid}/structure", json={"structure": bad}).status_code == 422
    # the user's material library
    m = {"name": "Test ply", "kind": "lamina", "density": 1500, "E1": 120e9, "E2": 8e9, "G12": 4e9, "nu12": 0.3,
         "Xt": 1.8e9, "Xc": 1.1e9, "Yt": 40e6, "Yc": 180e6, "S12": 70e6}
    assert client.put("/api/materials/test_ply", json=m).status_code == 200
    assert [x["id"] for x in client.get("/api/materials").json()["user"]] == ["test_ply"]
    assert client.put("/api/materials/cfrp_ud_t700", json=m).status_code == 400  # built-in ids are reserved
    assert client.put("/api/materials/odd", json=m | {"nu12": 0.95, "E2": 200e9}).status_code == 422
    assert client.delete("/api/materials/test_ply").status_code == 200
    assert client.get("/api/materials").json()["user"] == []
