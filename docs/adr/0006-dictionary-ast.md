# ADR-0006 — OpenFOAM dictionaries are generated from an AST via fork adapters

Status: Accepted (2026-10-06)

## Decision

Pipeline (MASTER §8):

`UI state → typed domain model → validation → versioned schema → fork/version adapter →
dictionary AST → deterministic writer → case on disk → provider`.

* `aerobench.foam.ast`: `Dict` (ordered), `List`, `Vector`, `Dimensioned`, `Uniform`,
  `Word`, `Str`, `Bool`, `Raw` (opaque verbatim text, used only to preserve unparsed
  content from imported cases), `Directive` (`#include`, `#includeEtc`).
* Writer is deterministic (stable ordering, fixed float formatting with `repr`-round-trip
  precision) so generated cases have stable hashes and golden-file tests are exact.
* Parser is tolerant: comments, `#include*`, `$macro`, regex keys, `#codeStream`
  blocks (kept as `Raw`) — used for importing cases and for Expert Mode round-trips.
* **Adapters** own fork semantics: `OpenCFDAdapter(v2606)` and `FoundationAdapter(v14)`.
  Differences (solver invocation `simpleFoam` vs `foamRun -solver incompressibleFluid`,
  `turbulenceProperties` vs `momentumTransport`, `transportProperties` vs
  `physicalProperties`, function-object names/keywords) live only in adapters.
* **Expert overrides** are structured patches `(file, key-path, value|delete)` stored in
  the project and re-applied after every regeneration; conflicts (key no longer present)
  are reported, never dropped. Whole-file replacements are allowed and flagged.
* Generated cases never use dynamic code (ADR-0002).
