"""Engine service entry point: ``python -m aerobench.api.server``.

Prints one machine-readable line once listening::

    AEROBENCH_READY {"port": 51234, "token": "..."}

The Electron main process reads it from stdout.  Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=os.environ.get("AEROBENCH_TOKEN") or "")
    ap.add_argument("--dev", action="store_true", help="enable CORS for the Vite dev server and /api/docs")
    ap.add_argument("--no-token", action="store_true", help="development only: disable authentication")
    ap.add_argument("--static", default=None, help="serve the built desktop renderer from this directory")
    args = ap.parse_args(argv)
    token = "" if args.no_token else (args.token or secrets.token_urlsafe(32))
    port = args.port or _free_port()

    import uvicorn

    from .app import create_app

    app = create_app(token=token, dev_cors=args.dev, static_dir=args.static)
    print("AEROBENCH_READY " + json.dumps({"port": port, "token": token, "pid": os.getpid()}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
