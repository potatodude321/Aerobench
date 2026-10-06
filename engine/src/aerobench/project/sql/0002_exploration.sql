-- Design exploration bookkeeping (studies, optimisation, reports).
ALTER TABLE studies ADD COLUMN definition_id TEXT;
ALTER TABLE studies ADD COLUMN base_config_json TEXT;
ALTER TABLE studies ADD COLUMN started_at TEXT;
ALTER TABLE studies ADD COLUMN ended_at TEXT;
ALTER TABLE studies ADD COLUMN message TEXT;
ALTER TABLE designs ADD COLUMN token TEXT;
ALTER TABLE designs ADD COLUMN unit_json TEXT;
ALTER TABLE designs ADD COLUMN told INTEGER NOT NULL DEFAULT 0;
ALTER TABLE designs ADD COLUMN convergence_json TEXT;
ALTER TABLE designs ADD COLUMN geometry_check_json TEXT;
ALTER TABLE designs ADD COLUMN notes_json TEXT;
CREATE INDEX designs_status ON designs(study_id, status);
CREATE TABLE reports (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT,
    kind TEXT NOT NULL,              -- run | compare | study
    spec_json TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,            -- completed | failed
    message TEXT
);
