# Contributing to AeroBench

Thanks for helping. AeroBench is meant to make OpenFOAM and CalculiX usable for people who are
new to CAE, so clear behaviour and honest results matter more than features.

## Ways to help

* **Report a problem** — open an issue with the *Bug report* form. Attach the job log
  (*Logs* page, or the *log* link next to a failed run) and, if you can, the geometry or a
  simplified version of it. Say which AeroBench version and Windows version you use.
* **Suggest something** — open an issue with the *Feature request* form and describe what
  you are trying to do, not only the button you would like.
* **Fix or build something** — for anything bigger than a small fix, open an issue first so we
  can agree on the approach before you spend time on it.
* **Validation data** — comparisons against wind-tunnel or published results are very
  welcome (see [docs/validation.md](docs/validation.md) and [docs/benchmarks.md](docs/benchmarks.md)).

## Setting up

Follow [docs/developer-setup.md](docs/developer-setup.md). In short:

```bash
cd engine && python -m venv .venv && . .venv/bin/activate && pip install -e .[dev,cad]
python -m pytest tests/unit          # about two minutes, no OpenFOAM needed
cd ../desktop && npm ci && npm run typecheck && npm test
```

The integration tests (`engine/tests/integration`) need OpenFOAM v2606 and ParaView 6.2;
run them when you change case generation, execution or post-processing. Building the Windows
installer is described in [docs/packaging.md](docs/packaging.md).

## Making a change

1. Fork the repository and create a branch from `main`.
2. Keep each pull request to one topic. Add or update tests for what you change: engine
   tests live in `engine/tests/unit`, desktop tests next to the code (`*.test.ts`).
3. Before you push:
   * `ruff check src tests` in `engine/`
   * `python -m pytest tests/unit` in `engine/`
   * `npm run typecheck` and `npm test` in `desktop/`
4. Update the documentation that describes the behaviour you changed (the user guide for
   anything a user sees; an ADR in `docs/adr/` for a design decision).
5. Open the pull request and fill in the template. The automatic checks must pass.

## Ground rules for the code

* **Never fake results.** Every number shown comes from solver output; missing data is shown
  as missing; unconverged or failed runs are labelled as such everywhere.
* **Traceability.** Generated files, commands and settings stay recorded and reproducible.
* **Explicit conventions.** Coordinates and force signs are defined once
  ([ADR-0007](docs/adr/0007-coordinates-and-sign-conventions.md)); use them.
* **Plain words in the interface.** Messages say what happened and what to do next, in the
  terms of the user's task rather than the solver's internals.
* Python: typed, formatted to `ruff`'s rules. TypeScript: strict mode, no `any` in new
  interfaces where a type is known.

## Licence of contributions

By submitting a contribution you agree that it is licensed under the same licence as the
project ([LICENSE](LICENSE)), and that you have the right to submit it.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
