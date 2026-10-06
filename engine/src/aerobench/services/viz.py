"""Visualisation service: cached ParaView extractions, presets, scenes, screenshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import sha256_json
from ..core.ids import short_id, utc_now_iso
from ..domain.project import ProjectConfig
from ..project.db import dec
from ..project.store import Project
from ..viz.client import SidecarPool
from ..viz.presets import FIELD_INFO, automotive_presets, pvpython_script
from .context import AppContext
from .runs import run_record

OPS = {"surface", "slice", "streamlines", "isosurface"}


class VizService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._pool: Optional[SidecarPool] = None

    def available(self) -> dict[str, Any]:
        return self.ctx.paraview()

    def pool(self) -> SidecarPool:
        if self._pool is None:
            pv = self.ctx.paraview()
            if not pv.get("available"):
                raise AeroBenchError.make("visualisation.paraview_missing", ErrorCategory.DEPENDENCY,
                                          "ParaView is not available for post-processing.",
                                          action=pv.get("message") or "Install ParaView 6.2.")
            self._pool = SidecarPool(pv["pvpython"], size=2)
        return self._pool

    def close(self) -> None:
        if self._pool:
            self._pool.close_all()

    def _session(self, project: Project, run_id: str):
        r = run_record(project, run_id, include_snapshot=True)
        if r["status"] not in ("completed", "stopped", "interrupted", "failed"):
            raise AeroBenchError.make("visualisation.run_not_ready", ErrorCategory.VISUALISATION, "The run has no results to visualise yet.")
        cfg = ProjectConfig.model_validate(r["config_snapshot"])
        foam = project.root / r["path"] / "case.foam"
        key = f"{project.root}:{run_id}"
        sc = self.pool().get(key)
        if sc.case != str(foam):
            info = sc.request("open_case", foam_file=str(foam), u_inf=cfg.operating.speed,
                              l_ref=(r.get("reference") or {}).get("length") or cfg.reference.length or 1.0)
            sc.case = str(foam)
            sc.info = info  # type: ignore[attr-defined]
        return sc, r, cfg

    def presets(self, project: Project, run_id: str) -> list[dict[str, Any]]:
        r = run_record(project, run_id, include_snapshot=True)
        cfg = ProjectConfig.model_validate(r["config_snapshot"])
        ground = (r.get("resolved") or {}).get("ground_z", 0.0)
        return automotive_presets(cfg, ground)

    def extract(self, project: Project, run_id: str, op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op not in OPS:
            raise AeroBenchError.make("visualisation.bad_op", ErrorCategory.VISUALISATION, f"Unknown extraction '{op}'.")
        r = run_record(project, run_id)
        key = sha256_json({"run": run_id, "iterations": r.get("iterations"), "op": op, "params": params, "v": 2})[:24]
        cache = project.cache_dir("viz", run_id)
        out = cache / f"{key}.abg"
        if out.exists():
            from ..viz.payload import read_header

            return {"key": key, "header": read_header(out), "cached": True}
        sc, _, _ = self._session(project, run_id)
        header = sc.request(op, out=str(out), **params)
        return {"key": key, "header": header, "cached": False}

    def payload_path(self, project: Project, run_id: str, key: str) -> Path:
        if not key.isalnum():
            raise AeroBenchError.make("visualisation.bad_key", ErrorCategory.VISUALISATION, "Invalid payload key.")
        p = project.root / "cache" / "viz" / run_id / f"{key}.abg"
        if not p.exists():
            raise AeroBenchError.make("visualisation.payload_missing", ErrorCategory.VISUALISATION, "Visualisation payload not found.")
        return p

    def line_sample(self, project: Project, run_id: str, **params) -> dict[str, Any]:
        sc, _, _ = self._session(project, run_id)
        return sc.request("line_sample", **params)

    def probe(self, project: Project, run_id: str, **params) -> dict[str, Any]:
        sc, _, _ = self._session(project, run_id)
        return sc.request("probe", **params)

    def screenshot(self, project: Project, run_id: str, items: list[dict], camera: Optional[dict] = None,
                   size: tuple[int, int] = (1600, 900), name: Optional[str] = None, out_dir: Optional[Path] = None) -> Path:
        sc, _, _ = self._session(project, run_id)
        out_dir = out_dir or project.cache_dir("images", run_id)
        out = Path(out_dir) / f"{name or short_id('img')}.png"
        sc.request("screenshot", out=str(out), items=items, camera=camera, size=list(size))
        return out

    def preset_screenshot(self, project: Project, run_id: str, preset_id: str, out_dir: Optional[Path] = None,
                          size: tuple[int, int] = (1600, 900)) -> Optional[Path]:
        pr = next((p for p in self.presets(project, run_id) if p["id"] == preset_id), None)
        if pr is None or pr["op"] not in ("surface", "slice"):
            return None
        r = run_record(project, run_id, include_snapshot=True)
        cfg = ProjectConfig.model_validate(r["config_snapshot"])
        items = []
        patches = [c.id for c in cfg.geometry.components]
        field = pr["params"]["field"]
        info = FIELD_INFO.get(field, {})
        rng = pr.get("range") or info.get("range")
        if pr["op"] == "surface":
            items.append({"kind": "surface", "patches": pr["params"]["patches"], "field": field, "range": rng,
                          "colormap": info.get("colormap", "Cool to Warm"), "title": info.get("label", field)})
        else:
            items.append({"kind": "surface", "patches": patches, "field": "Cp", "range": [-1.5, 1.0], "scalar_bar": False,
                          "colormap": "Cool to Warm"})
            items.append({"kind": "slice", **{k: pr["params"][k] for k in ("origin", "normal", "field", "clip_box") if k in pr["params"]},
                          "range": rng, "colormap": info.get("colormap", "Viridis (matplotlib)"), "title": info.get("label", field)})
        s = cfg.geometry.summary
        cx = 0.5 * (s.bbox_min[0] + s.bbox_max[0]) if s else 2.0
        L = (s.bbox_max[0] - s.bbox_min[0]) if s else 4.5
        cam = _camera_for(pr, cx, L, s)
        return self.screenshot(project, run_id, items, cam, size, name=preset_id, out_dir=out_dir)

    # ------------------------------------------------------------------ scenes (application-level state)
    def save_scene(self, project: Project, scene: dict[str, Any], scene_id: Optional[str] = None, name: Optional[str] = None) -> str:
        sid = scene_id or short_id("scene")
        now = utc_now_iso()
        if project.db.one("SELECT id FROM scenes WHERE id = ?", (sid,)):
            project.db.update("scenes", "id", sid, {"scene_json": scene, "name": name or scene.get("name", sid), "updated_at": now})
        else:
            project.db.insert("scenes", {"id": sid, "name": name or scene.get("name", sid), "scene_json": scene, "created_at": now, "updated_at": now})
        return sid

    def scenes(self, project: Project) -> list[dict[str, Any]]:
        return [dec(r) for r in project.db.all("SELECT * FROM scenes ORDER BY updated_at DESC")]

    def expert_script(self, project: Project, run_id: str, preset_ids: list[str]) -> str:
        r = run_record(project, run_id, include_snapshot=True)
        cfg = ProjectConfig.model_validate(r["config_snapshot"])
        sel = [p for p in self.presets(project, run_id) if p["id"] in preset_ids]
        return pvpython_script(str(project.root / r["path"] / "case.foam"), sel, cfg.operating.speed)


def _camera_for(preset: dict, cx: float, L: float, s) -> dict[str, Any]:
    zc = 0.5 * (s.bbox_min[2] + s.bbox_max[2]) if s else 0.7
    if preset["op"] == "slice":
        n = preset["params"]["normal"]
        o = preset["params"]["origin"]
        if abs(n[1]) > 0.5:  # side view
            return {"position": [cx + 0.2 * L, -1.9 * L, zc], "focal_point": [cx + 0.2 * L, 0, zc], "view_up": [0, 0, 1]}
        if abs(n[0]) > 0.5:  # looking upstream from behind
            return {"position": [o[0] + 2.0 * L, o[1], o[2]], "focal_point": o, "view_up": [0, 0, 1]}
        return {"position": [cx, 0, -2.0 * L], "focal_point": [cx, 0, 0], "view_up": [1, 0, 0]}  # from below
    return {"position": [cx - 1.2 * L, -1.4 * L, zc + 0.8 * L], "focal_point": [cx, 0, zc], "view_up": [0, 0, 1]}
