-- Structural analyses of aero elements (CalculiX), one row per analysis.
CREATE TABLE structure_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    label TEXT,
    status TEXT NOT NULL,            -- queued | running | completed | failed | cancelled
    job_id TEXT,
    run_id TEXT,                     -- CFD run whose surface loads are applied
    path TEXT NOT NULL,              -- relative to the project root
    config_snapshot_json TEXT NOT NULL,
    summary_json TEXT,
    message TEXT
);
CREATE INDEX structure_runs_created ON structure_runs(created_at)
