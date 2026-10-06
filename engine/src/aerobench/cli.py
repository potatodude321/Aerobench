"""Command-line entry point: ``aerobench <command>``.

* ``serve``      start the engine HTTP service (used by the desktop shell)
* ``doctor``     report the solver/ParaView/MPI environment and exit non-zero if unusable
* ``worker``     run the background worker for a project folder
* ``benchmark``  list or run validation benchmarks (``benchmark run ahmed25 --level medium``)
* ``version``    print versions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _doctor(args) -> int:
    from .services.context import AppContext

    ctx = AppContext()
    rep = ctx.environment_report()
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        ok = False
        print("Solver environments:")
        for p in rep["providers"]:
            ok = ok or p["available"]
            mark = "OK " if p["available"] else "-- "
            print(f"  [{mark}] {p['label']}  ({p['id']}, {p['kind']})")
            if p.get("project_dir"):
                print(f"         {p['project_dir']}")
            mpi = p.get("mpi") or {}
            print(f"         MPI: {mpi.get('kind') or 'not found'} {mpi.get('version') or ''}".rstrip())
            for d in p.get("problems") or []:
                print(f"         ! {d['summary']}" + (f" — {d['action']}" if d.get("action") else ""))
        pv = rep["paraview"] or {}
        print(f"ParaView: {pv.get('version') or 'not found'}  {pv.get('pvpython') or ''}".rstrip())
        hw = rep["hardware"]
        print(f"Hardware: {hw.get('physical_cores')} physical cores, {hw.get('memory_gb')} GB RAM, {hw.get('os')}")
        print(f"Cores available to runs: {rep['max_cores']}")
        if not ok:
            print("\nNo usable OpenFOAM installation was found.", file=sys.stderr)
    return 0 if any(p["available"] for p in rep["providers"]) else 2


def _benchmark(args) -> int:
    from .validation.benchmarks import BENCHMARKS, list_benchmarks, run_benchmark

    if args.action == "list" or not args.case:
        for b in list_benchmarks():
            refs = "; ".join(f"{r['quantity']}={r['value']} ({r['source']})" if r.get("value") is not None else r["notes"] for r in b["references"])
            print(f"{b['id']:10s} {b['name']}\n           {refs}")
        return 0
    if args.case not in BENCHMARKS:
        print(f"unknown benchmark '{args.case}'", file=sys.stderr)
        return 2
    ev = run_benchmark(args.case, Path(args.out), args.level, args.cores, progress=lambda m: print(m, flush=True))
    print(json.dumps({k: ev[k] for k in ("case", "level", "status", "convergence", "checks", "comparisons", "cells", "project")}, indent=2, default=str))
    return 0 if ev["status"] == "pass" else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="aerobench", description="Automotive CFD workbench engine")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start the engine service (arguments are passed through)", add_help=False)
    d = sub.add_parser("doctor", help="check the solver / ParaView / MPI environment")
    d.add_argument("--json", action="store_true")
    w = sub.add_parser("worker", help="run the background worker for a project")
    w.add_argument("project")
    b = sub.add_parser("benchmark", help="validation benchmarks")
    b.add_argument("action", choices=["list", "run"])
    b.add_argument("case", nargs="?")
    b.add_argument("--level", default="medium", choices=["coarse", "medium", "fine"])
    b.add_argument("--out", default="aerobench-benchmarks")
    b.add_argument("--cores", type=int, default=None)
    sub.add_parser("version")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        from .api.server import main as serve

        return serve(argv[1:])
    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        return _doctor(args)
    if args.cmd == "worker":
        from .jobs.worker import main as worker

        return worker([args.project])
    if args.cmd == "benchmark":
        return _benchmark(args)
    if args.cmd == "version":
        from .version import APP_VERSION, GENERATOR_VERSION

        print(f"aerobench {APP_VERSION} (case generator {GENERATOR_VERSION})")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
