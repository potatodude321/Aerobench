# ADR-0005 — Detached job runners with file-based state

Status: Accepted (2026-10-06)

## Context

"Do not lose a 12-hour simulation because the GUI crashed" (BUILD_SPEC §13). The solver
must run outside the UI process and outside the engine service process.

## Decision

* Each job (mesh, solve, post) is executed by a **runner process**
  (`python -m aerobench.jobs.runner <job-dir>`) launched detached
  (Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`;
  POSIX: `start_new_session=True`). It survives the engine and the GUI.
* The runner is the **single writer** of its job directory:
  * `job.json` — immutable job specification (ordered steps, each a provider-neutral
    command description + success criteria).
  * `state.json` — atomically replaced: status, current step, child PID + create time,
    runner PID + create time, heartbeat timestamp, exit codes, error classification.
  * `events.jsonl` — append-only event stream.
  * `logs/<nn>-<step>.log` — raw stdout/stderr per step (never discarded).
* Control is file-based: the engine writes `control.json` (`{"request": "stop"|"kill"}`).
  Graceful stop uses OpenFOAM's `abort` function object (trigger file containing
  `action=writeNow`), which every generated controlDict includes; forced stop kills the
  process tree.
* The engine mirrors job state into SQLite and streams log deltas to the UI by tailing
  files. On engine start it reconciles every non-terminal job: alive runner (PID +
  create-time match) → reattach; dead runner with terminal state → ingest; dead runner
  without terminal state → `interrupted`, with `resumable=true` when a time directory
  exists.
* Resume = new job that continues from `latestTime` with the same frozen configuration.

## Consequences

* No IPC socket between engine and runner; nothing to reconnect.
* Heartbeat staleness (> 60 s with a dead PID) is the only signal for "lost".
