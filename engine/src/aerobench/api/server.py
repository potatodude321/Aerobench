"""Engine service entry point: ``python -m aerobench.api.server``.

Prints one machine-readable line once the socket is listening::

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


def _listening_socket(port: int) -> socket.socket:
    """Bind and listen *before* announcing the port: a client that connects as soon as it reads
    the READY line is queued by the kernel until the server accepts, never refused (and no
    other process can take a port that was only probed)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(128)
    s.set_inheritable(False)
    return s


def main(argv: list[str] | None = None) -> int:
    # FastAPI depends on the OpenTelemetry *API* only (a no-op without an SDK and exporter,
    # none of which ship).  Disable it explicitly as well: the engine sends nothing anywhere.
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=os.environ.get("AEROBENCH_TOKEN") or "")
    ap.add_argument("--dev", action="store_true", help="enable CORS for the Vite dev server and /api/docs")
    ap.add_argument("--no-token", action="store_true", help="development only: disable authentication")
    ap.add_argument("--static", default=None, help="serve the built desktop renderer from this directory")
    args = ap.parse_args(argv)
    token = "" if args.no_token else (args.token or secrets.token_urlsafe(32))

    import uvicorn

    from .app import create_app

    app = create_app(token=token, dev_cors=args.dev, static_dir=args.static)
    sock = _listening_socket(args.port)
    port = sock.getsockname()[1]
    print("AEROBENCH_READY " + json.dumps({"port": port, "token": token, "pid": os.getpid()}), flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
