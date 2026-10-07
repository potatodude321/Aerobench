/** HTTP + WebSocket client for the local engine service. */
import { bridge } from "./bridge";

export interface Diagnostic {
  code: string;
  category: string;
  severity: "info" | "warning" | "error";
  summary: string;
  likely_cause?: string;
  action?: string;
  details?: string;
  context?: Record<string, unknown>;
}

export class EngineError extends Error {
  diagnostic: Diagnostic;
  status: number;
  constructor(status: number, diagnostic: Diagnostic) {
    super(diagnostic.summary);
    this.status = status;
    this.diagnostic = diagnostic;
  }
}

let baseUrl = "";
let token = "";

export async function initEngine(): Promise<{ url: string; token: string }> {
  if (bridge) {
    const info = await bridge.getEngine();
    if (info) {
      baseUrl = info.url;
      token = info.token;
    }
  } else {
    // Browser development: same origin (Vite proxy) or explicit ?engine= / ?token=
    const q = new URLSearchParams(window.location.search);
    baseUrl = q.get("engine") || (import.meta.env.VITE_ENGINE_URL as string) || "";
    token = q.get("token") || (import.meta.env.VITE_ENGINE_TOKEN as string) || "";
  }
  return { url: baseUrl, token };
}

export function engineUrl(path: string): string {
  return baseUrl + path;
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra || {}) };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) {
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return (await r.json()) as T;
    return (await r.text()) as unknown as T;
  }
  let diag: Diagnostic = { code: `http.${r.status}`, category: "internal", severity: "error", summary: r.statusText || "Request failed" };
  try {
    const body = await r.json();
    if (body?.error) diag = body.error as Diagnostic;
  } catch {
    /* non-JSON error */
  }
  throw new EngineError(r.status, diag);
}

/**
 * Mutations are applied strictly in the order the user made them.  A field commits on blur,
 * which fires on mouse-down of the next click: an edit and a "Run simulation" click therefore
 * leave together, and without ordering the engine could snapshot the configuration before the
 * edit lands.  Each mutation waits for the previous one to finish (success or failure).
 * Read-only POSTs (previews, comparisons, ParaView renders, report generation, exports) are
 * not queued, so a slow render never delays an edit.
 */
const UNQUEUED = [/\/preview(\.abg)?$/, /\/compare$/, /\/viz\//, /\/reports$/, /\/geometry\/export$/];
let mutationChain: Promise<unknown> = Promise.resolve();

export function ordered<T>(path: string, fn: () => Promise<T>): Promise<T> {
  if (UNQUEUED.some((re) => re.test(path))) return fn();
  const run = mutationChain.then(fn, fn);
  mutationChain = run.catch(() => undefined);
  return run;
}

/** Resolves when every mutation issued so far has been applied. */
export function settled(): Promise<void> {
  return mutationChain.then(() => undefined);
}

export const api = {
  async get<T = any>(path: string): Promise<T> {
    return handle<T>(await fetch(engineUrl(path), { headers: headers() }));
  },
  post<T = any>(path: string, body?: unknown): Promise<T> {
    return ordered(path, async () =>
      handle<T>(
        await fetch(engineUrl(path), {
          method: "POST",
          headers: headers({ "Content-Type": "application/json" }),
          body: body === undefined ? undefined : JSON.stringify(body),
        }),
      ),
    );
  },
  patch<T = any>(path: string, body: unknown): Promise<T> {
    return ordered(path, async () =>
      handle<T>(
        await fetch(engineUrl(path), { method: "PATCH", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) }),
      ),
    );
  },
  put<T = any>(path: string, body: unknown): Promise<T> {
    return ordered(path, async () =>
      handle<T>(
        await fetch(engineUrl(path), { method: "PUT", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) }),
      ),
    );
  },
  async binary(path: string): Promise<ArrayBuffer> {
    const r = await fetch(engineUrl(path), { headers: headers() });
    if (!r.ok) await handle(r);
    return r.arrayBuffer();
  },
  async postBinary(path: string, body: unknown): Promise<ArrayBuffer> {
    const r = await fetch(engineUrl(path), { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) });
    if (!r.ok) await handle(r);
    return r.arrayBuffer();
  },
  upload<T = any>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    return ordered(path, async () => handle<T>(await fetch(engineUrl(path), { method: "POST", headers: headers(), body: fd })));
  },
  imageUrl(path: string): string {
    // <img> cannot send headers: pass the token as a query parameter (localhost only)
    const sep = path.includes("?") ? "&" : "?";
    return engineUrl(path) + (token ? `${sep}token=${encodeURIComponent(token)}` : "");
  },
};

type Listener = (msg: any) => void;

/** Event stream with automatic reconnection. */
export class EventStream {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private pid: string | null = null;
  private logs = new Set<string>();
  private closed = false;

  connect(): void {
    this.closed = false;
    const http = baseUrl || window.location.origin;
    const url = http.replace(/^http/, "ws") + `/api/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      if (this.pid) ws.send(JSON.stringify({ type: "subscribe_project", pid: this.pid }));
      for (const j of this.logs) ws.send(JSON.stringify({ type: "subscribe_log", job_id: j }));
    };
    ws.onmessage = (ev) => {
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      for (const l of this.listeners) l(msg);
    };
    ws.onclose = () => {
      this.ws = null;
      if (!this.closed) setTimeout(() => this.connect(), 1500);
    };
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }

  private send(obj: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  subscribeProject(pid: string): void {
    this.pid = pid;
    this.send({ type: "subscribe_project", pid });
  }

  subscribeLog(jobId: string): void {
    this.logs.add(jobId);
    this.send({ type: "subscribe_log", job_id: jobId });
  }

  unsubscribeLog(jobId: string): void {
    this.logs.delete(jobId);
    this.send({ type: "unsubscribe_log", job_id: jobId });
  }

  on(l: Listener): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }
}

export const events = new EventStream();
