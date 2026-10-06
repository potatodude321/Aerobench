"""Automotive visualisation presets → declarative ParaView pipeline specs (BUILD_SPEC §17).

Each preset is a plain dict the sidecar understands; the same spec drives the in-app 3D
view (extracted geometry), report screenshots and the exported Expert-Mode pvpython
script, so all three show the same thing.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.project import ProjectConfig

FIELD_INFO = {
    "Cp": {"label": "Pressure coefficient Cp", "colormap": "Cool to Warm", "range": [-1.5, 1.0], "units": "-"},
    "p": {"label": "Kinematic pressure p", "colormap": "Cool to Warm", "range": None, "units": "m²/s²"},
    "U": {"label": "Velocity |U|", "colormap": "Viridis (matplotlib)", "range": None, "units": "m/s"},
    "U_ratio": {"label": "|U| / U∞", "colormap": "Viridis (matplotlib)", "range": [0.0, 1.3], "units": "-"},
    "yPlus": {"label": "y+", "colormap": "Turbo", "range": [0.0, 300.0], "units": "-"},
    "wallShearStress": {"label": "Wall shear stress |τw|/ρ", "colormap": "Inferno (matplotlib)", "range": None, "units": "m²/s²"},
    "k": {"label": "Turbulent kinetic energy k", "colormap": "Plasma (matplotlib)", "range": None, "units": "m²/s²"},
    "nut": {"label": "Turbulent viscosity νt", "colormap": "Plasma (matplotlib)", "range": None, "units": "m²/s"},
    "Q": {"label": "Q-criterion", "colormap": "Viridis (matplotlib)", "range": None, "units": "1/s²"},
}


def _vehicle(cfg: ProjectConfig, summary: Optional[dict] = None):
    s = cfg.geometry.summary
    lo = list(s.bbox_min) if s else [0, -1, 0]
    hi = list(s.bbox_max) if s else [4.5, 1, 1.4]
    if summary:
        lo, hi = summary.get("bbox_min", lo), summary.get("bbox_max", hi)
    return lo, hi


def vehicle_patches(cfg: ProjectConfig) -> list[str]:
    return [c.id for c in cfg.geometry.components]


def automotive_presets(cfg: ProjectConfig, ground_z: float = 0.0) -> list[dict[str, Any]]:
    lo, hi = _vehicle(cfg)
    L = hi[0] - lo[0]
    W = hi[1] - lo[1]
    H = hi[2] - ground_z
    yc = cfg.vehicle.centreline_y
    zc = ground_z + 0.5 * H
    patches = vehicle_patches(cfg)
    box_side = [[lo[0] - 0.4 * L, yc - 1, ground_z], [hi[0] + 1.2 * L, yc + 1, hi[2] + 0.5 * H]]
    presets: list[dict[str, Any]] = [
        {"id": "cp_body", "label": "Cp body map", "op": "surface", "params": {"patches": patches, "field": "Cp"}, "range": [-1.5, 1.0]},
        {"id": "yplus_body", "label": "y+ body map", "op": "surface", "params": {"patches": patches, "field": "yPlus"}, "range": [0.0, 300.0]},
        {"id": "wall_shear", "label": "Wall shear stress", "op": "surface", "params": {"patches": patches, "field": "wallShearStress"}},
        {"id": "centreline_cp", "label": "Centreline pressure", "op": "slice",
         "params": {"origin": [0.5 * (lo[0] + hi[0]), yc, zc], "normal": [0, 1, 0], "field": "Cp", "clip_box": box_side}, "range": [-1.5, 1.0]},
        {"id": "centreline_velocity", "label": "Centreline velocity", "op": "slice",
         "params": {"origin": [0.5 * (lo[0] + hi[0]), yc, zc], "normal": [0, 1, 0], "field": "U_ratio", "clip_box": box_side},
         "range": [0.0, 1.3]},
        {"id": "rear_wake", "label": "Rear wake plane", "op": "slice",
         "params": {"origin": [hi[0] + 0.25 * L, yc, zc], "normal": [1, 0, 0], "field": "U_ratio", "with_vectors": True,
                    "clip_box": [[hi[0], yc - 0.9 * W, ground_z], [hi[0] + 0.5 * L, yc + 0.9 * W, hi[2] + 0.4 * H]]},
         "range": [0.0, 1.2]},
    ]
    wheels = {w.position.value: w for w in cfg.vehicle.wheels}
    for axle, label in (("F", "Front-wheel wake"), ("R", "Rear-wheel wake")):
        ws = [w for k, w in wheels.items() if k.startswith(axle)]
        if ws:
            r = max(w.radius for w in ws)
            x = max(w.centre[0] for w in ws) + 1.3 * r
            presets.append({"id": f"{'front' if axle == 'F' else 'rear'}_wheel_wake", "label": label, "op": "slice",
                            "params": {"origin": [x, yc, ground_z + r], "normal": [1, 0, 0], "field": "U_ratio", "with_vectors": True,
                                       "clip_box": [[x - 0.1, yc - 0.75 * W, ground_z], [x + 0.1, yc + 0.75 * W, ground_z + 3 * r]]},
                            "range": [0.0, 1.2]})
    presets.append({"id": "underfloor_velocity", "label": "Underfloor velocity", "op": "slice",
                    "params": {"origin": [0.5 * (lo[0] + hi[0]), yc, ground_z + 0.04 * H], "normal": [0, 0, 1], "field": "U_ratio",
                               "clip_box": [[lo[0] - 0.2 * L, yc - 0.6 * W, ground_z], [hi[0] + 0.5 * L, yc + 0.6 * W, ground_z + 0.2 * H]]},
                    "range": [0.0, 1.3]})
    if cfg.vehicle.rear_axle_x is not None:
        xd = cfg.vehicle.rear_axle_x + 0.6 * (hi[0] - cfg.vehicle.rear_axle_x)
        presets.append({"id": "diffuser_plane", "label": "Diffuser plane", "op": "slice",
                        "params": {"origin": [xd, yc, zc], "normal": [1, 0, 0], "field": "U_ratio",
                                   "clip_box": [[xd - 0.1, yc - 0.6 * W, ground_z], [xd + 0.1, yc + 0.6 * W, ground_z + 0.45 * H]]},
                        "range": [0.0, 1.3]})
    presets.append({"id": "streamlines", "label": "Streamlines around vehicle", "op": "streamlines",
                    "params": {"p1": [lo[0] - 0.15 * L, yc - 0.45 * W, ground_z + 0.45 * H], "p2": [lo[0] - 0.15 * L, yc + 0.45 * W, ground_z + 0.45 * H],
                               "resolution": 40, "field": "U_ratio", "max_length": 3.0 * L}})
    presets.append({"id": "streamlines_vertical", "label": "Centreline streamlines", "op": "streamlines",
                    "params": {"p1": [lo[0] - 0.15 * L, yc, ground_z + 0.03 * H], "p2": [lo[0] - 0.15 * L, yc, hi[2] + 0.3 * H],
                               "resolution": 30, "field": "U_ratio", "max_length": 3.0 * L}})
    presets.append({"id": "q_wake", "label": "Q-criterion wake (Q·(L/U)² = 50)", "op": "isosurface",
                    "params": {"field": "Q", "values": [50.0], "color_field": "U_ratio"}, "range": [0.0, 1.3]})
    return presets


def pvpython_script(case_foam: str, presets: list[dict[str, Any]], u_inf: float) -> str:
    """Expert Mode: a standalone paraview.simple script reproducing the selected presets."""
    lines = [
        "# Generated by AeroBench — open with ParaView's Python shell or run with pvpython.",
        "from paraview.simple import *",
        f"surf = OpenFOAMReader(FileName={case_foam!r})",
        "vol = OpenFOAMReader(FileName=%r)" % case_foam,
        "vol.MeshRegions = ['internalMesh']",
        "view = GetActiveViewOrCreate('RenderView')",
    ]
    for i, p in enumerate(presets):
        pr = p["params"]
        v = f"item{i}"
        field = pr.get("field", "U")
        src = "vol"
        if field == "U_ratio":
            lines.append(f"{v}_calc = Calculator(Input={'surf' if p['op'] == 'surface' else 'vol'}, ResultArrayName='U_ratio', Function='mag(U)/{u_inf!r}')")
            src = f"{v}_calc"
        if p["op"] == "surface":
            lines.append(f"surf.MeshRegions = {['patch/' + x for x in pr['patches']]!r}")
            src = "surf" if src == "vol" else src
            lines.append(f"{v} = MergeBlocks(Input={src})")
        elif p["op"] == "slice":
            lines += [f"{v} = Slice(Input={src})", f"{v}.SliceType = 'Plane'", f"{v}.SliceType.Origin = {pr['origin']!r}",
                      f"{v}.SliceType.Normal = {pr['normal']!r}"]
        elif p["op"] == "streamlines":
            lines += [f"{v} = StreamTracer(Input=vol, SeedType='Line')", f"{v}.Vectors = ['POINTS', 'U']",
                      f"{v}.SeedType.Point1 = {pr['p1']!r}", f"{v}.SeedType.Point2 = {pr['p2']!r}",
                      f"{v}.SeedType.Resolution = {pr.get('resolution', 40)}"]
        elif p["op"] == "isosurface":
            lines += [f"{v}_grad = Gradient(Input=vol)", f"{v}_grad.ScalarArray = ['POINTS', 'U']", f"{v}_grad.ComputeQCriterion = 1",
                      f"{v}_grad.QCriterionArrayName = 'Q'", f"{v} = Contour(Input={v}_grad)", f"{v}.ContourBy = ['POINTS', 'Q']",
                      f"{v}.Isosurfaces = [{pr['values'][0]!r} * ({u_inf!r} / L_REF) ** 2]  # set L_REF"]
        lines += [f"d{i} = Show({v}, view)", f"ColorBy(d{i}, ('POINTS', {field!r}))"]
    lines += ["Render()"]
    return "\n".join(lines) + "\n"
