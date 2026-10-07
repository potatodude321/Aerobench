import { afterEach, describe, expect, it, vi } from "vitest";
import { api, settled } from "./client";

/** fetch stub: each request resolves after the delay given for its path; the order in which
 * requests *start* is recorded (a request starts when fetch is called). */
function stubFetch(delays: Record<string, number>, fail: string[] = []) {
  const started: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = url.replace(/^.*?(\/api\/)/, "$1");
    started.push(`${init?.method ?? "GET"} ${path}`);
    return new Promise((resolve) =>
      setTimeout(() => {
        const ok = !fail.includes(path);
        resolve(new Response(JSON.stringify(ok ? { path } : { error: { code: "x", summary: "boom" } }), {
          status: ok ? 200 : 500, headers: { "content-type": "application/json" },
        }));
      }, delays[path] ?? 0),
    );
  });
  return started;
}

afterEach(() => vi.unstubAllGlobals());

describe("mutation ordering", () => {
  it("starts a run only after an in-flight config edit has been applied", async () => {
    const started = stubFetch({ "/api/p/1/config": 50, "/api/p/1/runs": 0 });
    const edit = api.patch("/api/p/1/config", { numerics: { iterations: 300 } });
    const run = api.post("/api/p/1/runs", {});
    await Promise.all([edit, run]);
    expect(started).toEqual(["PATCH /api/p/1/config", "POST /api/p/1/runs"]);
  });

  it("keeps going after a failed mutation, in order", async () => {
    const started = stubFetch({ "/api/p/1/config": 20 }, ["/api/p/1/config"]);
    const edit = api.patch("/api/p/1/config", {}).catch((e) => e);
    const run = api.post("/api/p/1/runs", {});
    expect((await edit).status).toBe(500);
    await run;
    expect(started).toEqual(["PATCH /api/p/1/config", "POST /api/p/1/runs"]);
    await settled();
  });

  it("never queues stop/kill or study controls behind a slow mutation", async () => {
    stubFetch({ "/api/p/1/geometry/import": 80 });
    let importDone = false;
    const imp = api.post("/api/p/1/geometry/import", {}).then(() => (importDone = true));
    await api.post("/api/p/1/runs/run-1/stop", { force: true });
    await api.post("/api/p/1/studies/s-1/cancel");
    expect(importDone).toBe(false);
    await imp;
  });

  it("queues per project", async () => {
    stubFetch({ "/api/p/1/geometry/import": 80 });
    let importDone = false;
    const imp = api.post("/api/p/1/geometry/import", {}).then(() => (importDone = true));
    await api.patch("/api/p/2/config", {});
    expect(importDone).toBe(false);
    await imp;
    await settled();
  });

  it("does not queue read-only posts behind a slow mutation", async () => {
    stubFetch({ "/api/p/1/geometry/import": 80 });
    let importDone = false;
    const imp = api.post("/api/p/1/geometry/import", {}).then(() => (importDone = true));
    await api.post("/api/p/1/compare", {});
    expect(importDone).toBe(false);
    await imp;
  });
});
