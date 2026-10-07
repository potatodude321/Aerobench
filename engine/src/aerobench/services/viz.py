"""Visualisation service: cached ParaView extractions, presets, scenes, screenshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import sha256_json
from ..core.ids import check_id, short_id, utc_now_iso
from ..domain.project import ProjectConfig
from ..project.db import dec
from ..project.store import Project
from ..viz.client import SidecarPool
from ..viz.presets import FIELD_INFO, automotive_presets, pvpython_script
from .context import AppContext
from .runs import run_record

OPS = {"surface", "slice", "streamlines", "isosurface"}
MESH_OPS = {"mesh_patches", "mesh_slice"}


def _times(d: Path) -> list[float]:
    out = []
    try:
        for x in d.iterdir():
            try:
                out.append(float(x.name))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def case_signature(case_dir: Path) -> dict[str, Any]:
    """What the visualisation of a run depends on: the newest written time in the case and in
    processor0 (a stopped parallel run has results only there). Payload caches and open
    ParaView sessions are keyed by it, so resuming or reconstructing a run never shows stale
    pictures."""
    root = max([t for t in _times(case_dir) if t > 0], default=0.0)
    proc = max([t for t in _times(case_dir / "processor0") if t > 0], default=0.0)
    return {"root": root, "processor": proc}


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
        case_dir = project.root / r["path"]
        foam = case_dir / "case.foam"
        sig = case_signature(case_dir)
        if max(sig.values()) <= 0:
            raise AeroBenchError.make(
                "visualisation.no_solution", ErrorCategory.VISUALISATION,
                "This run has no saved solution to visualise — only the initial conditions.",
                likely_cause="It was stopped or killed before OpenFOAM wrote its first time step.",
                action="Resume the run (it writes its state when stopped gracefully) or run it again.")
        key = f"{project.root}:{run_id}"
        sc = self.pool().get(key)
        if sc.case != str(foam) or getattr(sc, "signature", None) != sig:
            info = sc.request("open_case", foam_file=str(foam), u_inf=cfg.operating.speed,
                              l_ref=(r.get("reference") or {}).get("length") or cfg.reference.length or 1.0)
            sc.case = str(foam)
            sc.signature = sig  # type: ignore[attr-defined]
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
        sig = case_signature(project.root / r["path"])
        key = sha256_json({"run": run_id, "iterations": r.get("iterations"), "case": sig, "op": op, "params": params, "v": 3})[:24]
        cache = project.cache_dir("viz", run_id)
        out = cache / f"{key}.abg"
        if out.exists():
            from ..viz.payload import read_header

            return {"key": key, "header": read_header(out), "cached": True}
        sc, _, _ = self._session(project, run_id)
        header = sc.request(op, out=str(out), **params)
        return {"key": key, "header": header, "cached": False}

    # ------------------------------------------------------------------ generated mesh
    def _mesh_row(self, project: Project, mesh_id: str) -> dict[str, Any]:
        row = dec(project.db.one("SELECT * FROM mesh_revisions WHERE id = ?", (check_id(mesh_id, "mesh id"),)))
        if row is None:
            raise AeroBenchError.make("mesh.unknown", ErrorCategory.PROJECT, f"Unknown mesh {mesh_id}.")
        return row

    @staticmethod
    def _mesh_signature(mdir: Path) -> str:
        """Changes whenever the mesh files change (re-meshing into the same folder never
        happens, but a cache must never show a different mesh)."""
        pm = mdir / "constant" / "polyMesh"
        parts = []
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            for cand in (pm / name, pm / f"{name}.gz"):
                if cand.exists():
                    st = cand.stat()
                    parts.append(f"{cand.name}:{st.st_size}:{st.st_mtime_ns}")
        return sha256_json(parts)[:16]

    def mesh_view(self, project: Project, mesh_id: str) -> dict[str, Any]:
        """What the mesh viewer can show for a generated mesh: patch groups, refinement plan
        (as used for this mesh, not the current settings), domain."""
        row = self._mesh_row(project, mesh_id)
        mdir = project.root / row["path"]
        has_mesh = (mdir / "constant" / "polyMesh" / "owner").exists() or (mdir / "constant" / "polyMesh" / "owner.gz").exists()
        if row["status"] in ("queued", "starting", "running") or not has_mesh:
            raise AeroBenchError.make("mesh.not_viewable", ErrorCategory.VISUALISATION,
                                      "This mesh cannot be shown yet.",
                                      likely_cause="Meshing has not finished (or did not produce a mesh).",
                                      action="Wait for meshing to finish, or generate the mesh again.")
        sc, info = self._mesh_session(project, mesh_id, row)
        patches = [r.split("/", 1)[1] for r in info.get("regions", []) if r.startswith("patch/")]
        cfg = ProjectConfig.model_validate(row["config_snapshot"]) if row.get("config_snapshot") else None
        comp_ids = {c.id for c in cfg.geometry.components} if cfg else set()
        vehicle = [x for x in patches if x in comp_ids]
        ground = [x for x in patches if x == "ground"]
        domain = [x for x in patches if x not in vehicle and x not in ground]
        if not vehicle:  # older meshes / unexpected naming: everything that is not the tunnel
            vehicle = [x for x in domain if x not in ("inlet", "outlet", "top", "side_left", "side_right", "symmetry", "front", "back")]
            domain = [x for x in domain if x not in vehicle]
        return {"mesh_id": mesh_id, "status": row["status"], "patches": {"vehicle": vehicle, "ground": ground, "domain": domain},
                "plan": row.get("plan"), "cells": row.get("cells")}

    def _mesh_session(self, project: Project, mesh_id: str, row: Optional[dict[str, Any]] = None):
        row = row or self._mesh_row(project, mesh_id)
        mdir = project.root / row["path"]
        foam = mdir / "case.foam"
        if not foam.exists():
            foam.write_text("")
        sig = self._mesh_signature(mdir)
        key = f"{project.root}:mesh:{mesh_id}"
        sc = self.pool().get(key)
        if sc.case != str(foam) or getattr(sc, "signature", None) != sig:
            sc.info = sc.request("open_case", foam_file=str(foam))  # type: ignore[attr-defined]
            sc.case = str(foam)
            sc.signature = sig  # type: ignore[attr-defined]
        return sc, sc.info  # type: ignore[attr-defined]

    def mesh_extract(self, project: Project, mesh_id: str, op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op not in MESH_OPS:
            raise AeroBenchError.make("visualisation.bad_op", ErrorCategory.VISUALISATION, f"Unknown mesh view '{op}'.")
        row = self._mesh_row(project, mesh_id)
        mdir = project.root / row["path"]
        params = dict(params)
        if op == "mesh_slice":
            plan = row.get("plan") or {}
            params["base_cell"] = float(plan.get("base_cell") or 0.0)
            try:
                params["origin"] = [float(x) for x in params["origin"]]
                params["normal"] = [float(x) for x in params["normal"]]
            except (KeyError, TypeError, ValueError):
                raise AeroBenchError.make("visualisation.bad_params", ErrorCategory.VISUALISATION,
                                          "A cut plane needs an origin and a normal.") from None
        elif op == "mesh_patches":
            if not params.get("patches"):
                raise AeroBenchError.make("visualisation.bad_params", ErrorCategory.VISUALISATION, "No patches selected.")
        key = sha256_json({"mesh": mesh_id, "sig": self._mesh_signature(mdir), "op": op, "params": params, "v": 1})[:24]
        out = project.cache_dir("viz", mesh_id) / f"{key}.abg"
        if out.exists():
            from ..viz.payload import read_header

            return {"key": key, "header": read_header(out), "cached": True}
        sc, _ = self._mesh_session(project, mesh_id, row)
        header = sc.request(op, out=str(out), **params)
        return {"key": key, "header": header, "cached": False}

    def paraview_gui_script(self, project: Project, run_id: str) -> Path:
        """A script for the ParaView *GUI* (``paraview --script=``) that opens this run the way
        AeroBench reads it: decomposed results when the run was not reconstructed, latest
        time, pressure shown. Opening ``case.foam`` directly would show the initial
        conditions of a stopped parallel run."""
        r = run_record(project, run_id)
        case_dir = project.root / r["path"]
        sig = case_signature(case_dir)
        ctype = "Decomposed Case" if sig["processor"] > sig["root"] else "Reconstructed Case"
        foam = case_dir / "case.foam"
        if not foam.exists():
            foam.write_text("")
        script = case_dir / "open_in_paraview.py"
        script.write_text(
            "# Generated by AeroBench: opens this run in ParaView at its latest saved time.\n"
            "from paraview.simple import *\n"
            f"r = OpenFOAMReader(registrationName={r['label'] or run_id!r}, FileName={str(foam)!r})\n"
            f"r.CaseType = {ctype!r}\n"
            "r.MeshRegions = ['internalMesh']\n"
            "r.Createcelltopointfiltereddata = 1\n"
            "r.UpdatePipelineInformation()\n"
            "scene = GetAnimationScene()\n"
            "scene.UpdateAnimationUsingDataTimeSteps()\n"
            "times = list(r.TimestepValues) if r.TimestepValues else [0.0]\n"
            "t = times[-1] if isinstance(times, list) else times\n"
            "scene.AnimationTime = t\n"
            "r.UpdatePipeline(t)\n"
            "view = GetActiveViewOrCreate('RenderView')\n"
            "view.ViewTime = t\n"
            "d = Show(r, view)\n"
            "view.Update()\n"
            "ColorBy(d, ('POINTS', 'p'))\n"
            "d.RescaleTransferFunctionToDataRange(True, False)\n"
            "d.SetScalarBarVisibility(view, True)\n"
            "view.CameraPosition = [-6, -8, 4]\n"
            "ResetCamera(view)\n"
            "Render(view)\n",
            encoding="utf-8")
        return script

    def payload_path(self, project: Project, run_id: str, key: str) -> Path:
        if not key.isalnum():
            raise AeroBenchError.make("visualisation.bad_key", ErrorCategory.VISUALISATION, "Invalid payload key.")
        p = project.root / "cache" / "viz" / check_id(run_id, "run or mesh id") / f"{key}.abg"
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
