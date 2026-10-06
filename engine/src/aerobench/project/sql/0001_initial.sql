-- AeroBench project database, schema 1.
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE geometry_revisions (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    created_at TEXT NOT NULL,
    label TEXT,
    kind TEXT NOT NULL DEFAULT 'baseline',       -- baseline | repaired | variant | parameterised | optimised | replacement
    source_filename TEXT,
    source_sha256 TEXT,
    blob_sha256 TEXT NOT NULL,
    units TEXT,
    operations_json TEXT NOT NULL DEFAULT '[]',
    report_json TEXT,
    n_faces INTEGER,
    region_names_json TEXT,
    parameters_json TEXT
);

CREATE TABLE mesh_revisions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    geometry_revision_id TEXT,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT,
    path TEXT NOT NULL,
    plan_json TEXT,
    quality_json TEXT,
    snappy_json TEXT,
    cells INTEGER,
    provider TEXT,
    foam_fork TEXT,
    foam_version TEXT,
    manifest_json TEXT,
    mesh_hash TEXT,
    config_snapshot_json TEXT,
    diagnostics_json TEXT,
    resolved_json TEXT
);
CREATE INDEX mesh_input_hash ON mesh_revisions(input_hash);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    label TEXT,
    status TEXT NOT NULL,
    job_id TEXT,
    mesh_revision_id TEXT,
    geometry_revision_id TEXT,
    config_hash TEXT,
    solve_hash TEXT,
    config_snapshot_json TEXT NOT NULL,
    operating_json TEXT,
    reference_json TEXT,
    provider TEXT,
    foam_fork TEXT,
    foam_version TEXT,
    paraview_version TEXT,
    app_version TEXT,
    hardware_json TEXT,
    env_digest_json TEXT,
    manifest_json TEXT,
    resolved_json TEXT,
    n_procs INTEGER,
    started_at TEXT,
    ended_at TEXT,
    wall_seconds REAL,
    iterations INTEGER,
    metrics_json TEXT,
    convergence_json TEXT,
    diagnostics_json TEXT,
    study_id TEXT,
    design_id TEXT,
    path TEXT NOT NULL,
    reproduces_run_id TEXT,
    resumed_from_run_id TEXT,
    notes TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX runs_study ON runs(study_id);
CREATE INDEX runs_mesh ON runs(mesh_revision_id);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    cores INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    target_kind TEXT,
    target_id TEXT,
    depends_on TEXT,
    job_dir TEXT NOT NULL,
    message TEXT,
    ingested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX jobs_status ON jobs(status);

CREATE TABLE studies (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- study | optimisation
    name TEXT,
    definition_json TEXT NOT NULL,
    status TEXT NOT NULL,            -- draft | running | paused | completed | failed | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    baseline_design_id TEXT,
    summary_json TEXT
);

CREATE TABLE designs (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    origin TEXT,                     -- baseline | doe | optimiser | user
    values_json TEXT NOT NULL,
    status TEXT NOT NULL,            -- pending | geometry | meshing | solving | completed | failed | skipped | cancelled
    attempts INTEGER NOT NULL DEFAULT 0,
    run_ids_json TEXT NOT NULL DEFAULT '[]',
    geometry_revision_id TEXT,
    metrics_json TEXT,
    objectives_json TEXT,
    constraints_json TEXT,
    feasible INTEGER,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX designs_study ON designs(study_id, idx);

CREATE TABLE optimiser_state (
    study_id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE scenes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scene_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT,
    data_json TEXT
);
CREATE INDEX events_t ON events(t);
