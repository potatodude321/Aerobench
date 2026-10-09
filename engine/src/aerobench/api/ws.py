"""WebSocket event stream: job state changes, log deltas and live run series.

The client subscribes to topics; the server polls files (cheap, robust — the runner is
a separate process) and pushes only changes.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..jobs.manager import JobManager
from .app import EngineState

router = APIRouter()


@router.websocket("/api/ws")
async def events(ws: WebSocket):
    e: EngineState = ws.app.state.engine
    token = ws.query_params.get("token", "")
    if e.token and not hmac.compare_digest(token, e.token):
        await ws.close(code=4401)
        return
    await ws.accept()
    subs: dict[str, Any] = {"project": None, "logs": {}, "jobs_sig": None}
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
                data = json.loads(msg)
                if data.get("type") == "subscribe_project":
                    subs["project"] = data["pid"]
                    subs["jobs_sig"] = None
                elif data.get("type") == "subscribe_log":
                    from ..core.ids import check_id

                    try:
                        subs["logs"][check_id(str(data.get("job_id")), "job id")] = {"offset": -1, "step": None}
                    except Exception:  # noqa: BLE001 - ignore malformed subscriptions
                        pass
                elif data.get("type") == "unsubscribe_log":
                    subs["logs"].pop(data["job_id"], None)
            except asyncio.TimeoutError:
                pass
            pid = subs["project"]
            if not pid:
                continue
            try:
                p = e.projects.get(pid)
            except Exception:  # noqa: BLE001
                continue
            jm = JobManager(p)
            rows = await asyncio.to_thread(lambda jm=jm: [dict(r) for r in jm.list(["queued", "starting", "running", "stopping"])])
            # "ingested" tells the UI when results have been read (a moment after the job ends)
            recent = await asyncio.to_thread(lambda p=p: p.db.all(
                "SELECT id, kind, target_id, status, ended_at, ingested FROM jobs ORDER BY created_at DESC LIMIT 20"))

            def _states(rows=rows, jm=jm):
                out = {}
                for r in rows:
                    st = jm.state(r["id"])
                    if st:
                        out[r["id"]] = {"status": st.status.value, "step_index": st.step_index,
                                        "steps": [{"name": s.name, "status": s.status} for s in st.steps], "message": st.message}
                return out

            states = await asyncio.to_thread(_states)
            sig = json.dumps([(r["id"], r["status"], (states.get(r["id"]) or {}).get("step_index")) for r in rows]
                             + [(r["id"], r["status"], r["ingested"]) for r in recent])
            if sig != subs["jobs_sig"]:
                subs["jobs_sig"] = sig
                await ws.send_text(json.dumps({"type": "jobs", "active": rows, "recent": recent, "states": states}))
            for jid, sub in list(subs["logs"].items()):
                res = await asyncio.to_thread(jm.log_tail, jid, None, sub["offset"])
                if res.get("text") and (res["offset"] != sub["offset"] or res.get("step") != sub["step"]):
                    if res.get("step") != sub["step"] and sub["step"] is not None:
                        await ws.send_text(json.dumps({"type": "log_step", "job_id": jid, "step": res.get("step")}))
                    sub["offset"], sub["step"] = res["offset"], res.get("step")
                    await ws.send_text(json.dumps({"type": "log", "job_id": jid, "step": res.get("step"), "text": res["text"]}))
                elif res.get("step") != sub["step"]:
                    sub["step"] = res.get("step")
                    sub["offset"] = -1 if res.get("step") is None else 0
    except WebSocketDisconnect:
        return
