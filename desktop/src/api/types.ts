/** Types mirroring the engine's JSON (subset used by the UI). */
import type { Diagnostic } from "./client";

export type Vec3 = [number, number, number];

export interface ValidationIssue {
  path: string;
  severity: "info" | "warning" | "error";
  message: string;
  code: string;
}

export interface GeometryComponent {
  id: string;
  name: string;
  source_regions: string[];
  role: string;
  wheel_position: "FL" | "FR" | "RL" | "RR" | null;
  include_in_forces: boolean;
  suggested: boolean;
}

export interface Wheel {
  position: "FL" | "FR" | "RL" | "RR";
  component_id: string | null;
  centre: Vec3;
  axis: Vec3;
  radius: number;
  width: number | null;
  rotation: "vehicle_speed" | "custom" | "stationary";
  custom_omega: number | null;
  source: string;
  detection_confidence: number | null;
}

export interface ProjectConfig {
  schema_version: number;
  project_id: string;
  name: string;
  created_at: string;
  modified_at: string;
  app_version: string;
  template_id: string | null;
  solver: { fork: string; version: string };
  execution: { provider_id: string | null; n_procs: number; decomposition: string; write_format: string; purge_write: number };
  geometry: {
    revision_id: string | null;
    transform: { unit_scale: number; forward_axis: string; up_axis: string; translation: Vec3; auto_place: boolean };
    components: GeometryComponent[];
    summary: { bbox_min: Vec3; bbox_max: Vec3; triangle_count: number; surface_area: number; region_names: string[] } | null;
  };
  vehicle: { category: string; wheels: Wheel[]; centreline_y: number; ride_height_offset: number; rake_deg: number };
  reference: {
    area: number | null;
    area_source: string;
    area_computed: number | null;
    area_computed_resolution: number | null;
    length: number | null;
    length_source: string;
    density: number;
    velocity: number | null;
    moment_centre: Vec3 | null;
    moment_centre_source: string;
    axes: "body" | "wind";
    locked: boolean;
  };
  operating: { speed: number; yaw_deg: number; ground: string; yaw_ground_mode: string; wheels_rotate: boolean; pressure: number };
  tunnel: {
    preset: string;
    upstream: number;
    downstream: number;
    lateral: number;
    top: number;
    symmetry: string;
    contact_patch_fraction: number;
    farfield_bc: string;
    design_yaw_range_deg: [number, number] | null;
  };
  mesh: Record<string, any>;
  physics: {
    regime: string;
    turbulence_model: string;
    fluid: { name: string; temperature_c: number; density: number; kinematic_viscosity: number };
    inlet_turbulence: { intensity: number; spec: string; viscosity_ratio: number; length_scale: number | null };
    wall_treatment: string;
  };
  numerics: {
    preset: string;
    preset_version: string;
    base_preset: string;
    iterations: number;
    first_order_iterations: number | null;
    potential_init: boolean;
    write_interval: number | null;
    averaging_fraction: number;
    stop_when_converged?: boolean;
    overrides: Record<string, unknown>;
  };
  observables: Record<string, any>;
  expert: { patches: any[]; file_overrides: any[]; extra_function_objects: Record<string, string>; notes: string | null };
  parameters: any[];
  parameter_values: Record<string, number>;
  studies: any[];
  optimisations: any[];
  notes: string;
  tags: string[];
}

/** none | current | running | stopped | failed | stale (only "stale" means the settings changed) */
export type StageState = "none" | "current" | "running" | "stopped" | "failed" | "stale";

export interface StageStatus {
  current: any;
  matching: any;
  latest: any;
  state: StageState;
  stale: boolean;
}

export interface ProjectPayload {
  project_id: string;
  root: string;
  config: ProjectConfig;
  validation: ValidationIssue[];
  staleness: {
    hashes: Record<string, string>;
    mesh: StageStatus;
    solve: StageStatus;
  };
  worker: any;
}

export interface Metrics {
  [k: string]: number | null;
}

export interface RunSummary {
  id: string;
  created_at: string;
  label: string;
  status: string;
  job_id: string;
  mesh_revision_id: string;
  geometry_revision_id: string;
  operating: ProjectConfig["operating"];
  provider: string;
  foam_version: string;
  n_procs: number;
  started_at: string | null;
  ended_at: string | null;
  wall_seconds: number | null;
  iterations: number | null;
  metrics: Metrics | null;
  convergence_status: string | null;
  balance_ill_conditioned: boolean | null;
  study_id: string | null;
  design_id: string | null;
  tags: string[];
  notes: string | null;
}

export interface JobStepState {
  name: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  exit_code: number | null;
  log: string | null;
  command: string[] | null;
  diagnostics: Diagnostic[];
}

export interface JobState {
  job_id: string;
  status: string;
  step_index: number;
  steps: JobStepState[];
  started_at: string | null;
  ended_at: string | null;
  message: string;
  diagnostics: Diagnostic[];
}

export interface MeshPlan {
  base_cell: number;
  body_level: number;
  body_cell: number;
  domain_min: Vec3;
  domain_max: Vec3;
  n_cells_block: [number, number, number];
  ground_z: number;
  location_in_mesh: Vec3;
  surfaces: { component_id: string; role: string; min_level: number; max_level: number; feature_level: number; cell_size: number; layers: boolean; n_layers: number }[];
  regions: { id: string; name: string; kind: string; level: number; cell_size: number; shape: any; auto: boolean; volume: number }[];
  distance_levels: [number, number][];
  layer_design: any;
  estimate_cells: number;
  estimate_range: [number, number];
  estimate_memory_gb: [number, number];
  blockage_ratio: number | null;
  vehicle_bbox_min: Vec3;
  vehicle_bbox_max: Vec3;
  yaw_range: [number, number];
  half_model: boolean;
  notes: string[];
  warnings: string[];
}
