# ADR-0004 — Project persistence and result database

Status: Accepted (2026-10-06)

## Decision

A project is a directory:

```
<Project>/
  project.json            canonical working configuration (versioned schema)
  project.db              SQLite (WAL): revisions, runs, metrics, jobs, studies,
                          optimisation history, scenes, events, notes
  geometry/blobs/<sha256>.stl   content-addressed, immutable geometry
  meshes/<mesh-id>/       generated mesh cases (immutable once complete)
  runs/<run-id>/          generated solver cases + logs + postProcessing
  jobs/<job-id>/          runner state (job.json, state.json, events.jsonl, logs)
  cache/                  derived, disposable (viz extractions, thumbnails)
  exports/, reports/
  .autosave/              rolling snapshots of project.json
```

* `project.json` holds the *working* configuration only. It has `schema_version`;
  loading runs ordered migrations (`aerobench.domain.migrations`) and keeps the original
  file as `project.json.v<N>.bak` before rewriting. Writes are atomic
  (temp + fsync + `os.replace`).
* Every run stores a **frozen snapshot** of the configuration it used plus SHA-256
  hashes of: configuration (canonical JSON), geometry blob(s), every generated
  dictionary, the mesh (`polyMesh` files), and the provider environment digest. This
  makes results reproducible and "Reproduce Run" trivial.
* Geometry revisions and mesh revisions are first-class rows. Results reference the
  exact revision. Variants are revisions with lineage (`parent_id`, `operation`).
* SQLite migrations are numbered SQL scripts applied in a transaction and recorded in
  `schema_migrations`.
* Staleness: each pipeline stage has an input hash computed from the subset of the
  configuration it depends on (geometry → mesh → solve → post). A stage whose stored
  input hash differs from the current one is *stale*; nothing is deleted.

## Consequences

* Projects are portable folders; the DB is the index, files are the artefacts.
* Changing a colour map never touches stage hashes; changing geometry marks mesh,
  solution and derived results stale.
