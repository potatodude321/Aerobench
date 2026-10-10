"""Reusable project templates (BUILD_SPEC §29).  Templates never contain geometry."""

from __future__ import annotations

import copy
from typing import Any

from .project import ProjectConfig

TEMPLATE_VERSION = "1"

TEMPLATES: dict[str, dict[str, Any]] = {
    "generic_road_car": {
        "label": "Generic Road Car",
        "description": "Balanced tunnel and mesh, moving ground, rotating wheels, k-ω SST, 40 m/s.",
        "config": {
            "vehicle": {"category": "road_car"},
            "operating": {"speed": 40.0, "yaw_deg": 0.0, "ground": "moving", "wheels_rotate": True},
            "tunnel": {"preset": "balanced", "upstream": 3.0, "downstream": 7.0, "lateral": 2.0, "top": 2.0},
            "mesh": {"preset": "balanced", "layers": {"mode": "target_yplus", "target_yplus": 50.0}},
            "numerics": {"preset": "balanced", "iterations": 2000},
        },
        "result_presets": ["cp_body", "centreline_cp", "rear_wake", "underfloor_velocity", "yplus_body"],
    },
    "formula_student": {
        "label": "Formula Student",
        "description": "Open-wheel car at 15 m/s; fine aero-element refinement for wings; balance-focused outputs.",
        "config": {
            "vehicle": {"category": "formula_student"},
            "operating": {"speed": 15.0, "ground": "moving", "wheels_rotate": True},
            "tunnel": {"preset": "balanced", "upstream": 3.0, "downstream": 6.0, "lateral": 2.0, "top": 2.0},
            "mesh": {"preset": "balanced", "layers": {"mode": "target_yplus", "target_yplus": 30.0}},
            "numerics": {"preset": "robust", "iterations": 2500},
        },
        "result_presets": ["cp_body", "front_wheel_wake", "rear_wake", "underfloor_velocity", "q_wake"],
    },
    "gt_race_car": {
        "label": "GT / Race Car",
        "description": "High speed (60 m/s), high-accuracy mesh around splitter/diffuser/wing.",
        "config": {
            "vehicle": {"category": "race_car"},
            "operating": {"speed": 60.0, "ground": "moving", "wheels_rotate": True},
            "tunnel": {"preset": "high_accuracy", "upstream": 4.0, "downstream": 10.0, "lateral": 3.0, "top": 3.0},
            "mesh": {"preset": "high_accuracy", "layers": {"mode": "target_yplus", "target_yplus": 50.0}},
            "numerics": {"preset": "balanced", "iterations": 3000},
        },
        "result_presets": ["cp_body", "underfloor_velocity", "diffuser_plane", "rear_wake", "q_wake"],
    },
    "aero_element": {
        "label": "Aero element (wing, splitter, flap)",
        "description": "One element studied alone, in free stream or over a moving ground; planform reference area; "
                       "structural check of the part with CalculiX under the computed loads.",
        "config": {
            "application": "aero_element",
            "vehicle": {"category": "bluff_body"},
            "operating": {"speed": 40.0, "yaw_deg": 0.0, "ground": "moving", "wheels_rotate": False},
            "tunnel": {"preset": "custom", "upstream": 4.0, "downstream": 8.0, "lateral": 2.0, "top": 4.0},
            "mesh": {"preset": "balanced", "layers": {"mode": "target_yplus", "target_yplus": 30.0}},
            "numerics": {"preset": "balanced", "iterations": 1500},
            "reference": {"area_source": "computed_planform", "length_source": "vehicle_length"},
        },
        "result_presets": ["cp_body", "centreline_cp", "rear_wake", "yplus_body"],
    },
    "half_car": {
        "label": "Half-Car Symmetry",
        "description": "Symmetry plane at the centreline (zero yaw only) — half the cells, same drag/lift/balance.",
        "config": {
            "operating": {"yaw_deg": 0.0},
            "tunnel": {"symmetry": "half"},
            "mesh": {"preset": "balanced"},
            "numerics": {"preset": "balanced", "iterations": 2000},
        },
        "result_presets": ["cp_body", "centreline_cp", "rear_wake"],
    },
    "full_car_yaw": {
        "label": "Full-Car Yaw Study",
        "description": "Full model, freestream far field, wake refinement sized for ±10° so one mesh serves the sweep.",
        "config": {
            "tunnel": {"symmetry": "full", "farfield_bc": "freestream", "design_yaw_range_deg": [-10.0, 10.0], "lateral": 2.5},
            "mesh": {"preset": "balanced"},
            "numerics": {"preset": "balanced", "iterations": 2000},
        },
        "result_presets": ["cp_body", "rear_wake", "q_wake"],
    },
    "high_downforce": {
        "label": "High-Downforce",
        "description": "Finer surfaces on aero elements, robust numerics for separated flow under wings and diffusers.",
        "config": {
            "operating": {"speed": 50.0},
            "mesh": {"preset": "high_accuracy", "layers": {"mode": "target_yplus", "target_yplus": 30.0}},
            "numerics": {"preset": "robust", "iterations": 3000},
        },
        "result_presets": ["cp_body", "underfloor_velocity", "diffuser_plane", "q_wake"],
    },
    "low_drag": {
        "label": "Low-Drag",
        "description": "Wake-focused refinement and high-fidelity numerics for drag-sensitive comparisons.",
        "config": {
            "operating": {"speed": 40.0},
            "tunnel": {"preset": "high_accuracy", "upstream": 4.0, "downstream": 10.0, "lateral": 3.0, "top": 3.0},
            "mesh": {"preset": "balanced"},
            "numerics": {"preset": "high_fidelity", "iterations": 3000},
        },
        "result_presets": ["cp_body", "rear_wake", "centreline_velocity", "q_wake"],
    },
}


def _deep_merge(base: dict, upd: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def apply_template(cfg: ProjectConfig, template_id: str) -> ProjectConfig:
    t = TEMPLATES[template_id]
    data = _deep_merge(cfg.to_json(), t["config"])
    data["template_id"] = template_id
    return ProjectConfig.model_validate(data)


def list_templates() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "description": v["description"], "result_presets": v["result_presets"],
             "config": v["config"], "version": TEMPLATE_VERSION} for k, v in TEMPLATES.items()]


deep_merge = _deep_merge
