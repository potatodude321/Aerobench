import { create } from "zustand";
import { api, EngineError, events } from "../api/client";
import type { ProjectPayload, RunSummary } from "../api/types";
import { notifyError } from "../components/notify";

export type Section =
  | "geometry"
  | "tunnel"
  | "mesh"
  | "physics"
  | "run"
  | "results"
  | "compare"
  | "studies"
  | "optimise"
  | "report"
  | "project"
  | "logs"
  | "expert"
  | "settings";

interface JobsSnapshot {
  active: any[];
  recent: any[];
  states: Record<string, any>;
}

interface AppState {
  engineReady: boolean;
  engineError: string | null;
  environment: any | null;
  project: ProjectPayload | null;
  section: Section;
  expertMode: boolean;
  runs: RunSummary[];
  selectedRunId: string | null;
  compareRunIds: string[];
  jobs: JobsSnapshot;
  busy: Record<string, boolean>;
  geometryVersion: number; // bump to force viewport reload
  /** job to select when the Logs page opens */
  logJobId: string | null;
  /** view settings of the Mesh page, kept while the project is open */
  meshView: Record<string, any>;
  setSection(s: Section): void;
  openLog(jobId: string): void;
  setMeshView(patch: Record<string, any>): void;
  setExpert(v: boolean): void;
  setEnvironment(e: any): void;
  setEngineReady(ok: boolean, err?: string | null): void;
  openProjectPayload(p: ProjectPayload): void;
  refreshProject(): Promise<void>;
  patchConfig(patch: Record<string, unknown>): Promise<boolean>;
  closeProject(): void;
  refreshRuns(): Promise<void>;
  selectRun(id: string | null): void;
  toggleCompare(id: string): void;
  setJobs(j: JobsSnapshot): void;
  setBusy(key: string, v: boolean): void;
  bumpGeometry(): void;
}

export const useApp = create<AppState>((set, get) => ({
  engineReady: false,
  engineError: null,
  environment: null,
  project: null,
  section: "geometry",
  expertMode: false,
  runs: [],
  selectedRunId: null,
  compareRunIds: [],
  jobs: { active: [], recent: [], states: {} },
  busy: {},
  geometryVersion: 0,
  logJobId: null,
  meshView: {},
  setSection: (section) => set({ section }),
  openLog: (jobId) => set({ logJobId: jobId, section: "logs" }),
  setMeshView: (patch) => set((s) => ({ meshView: { ...s.meshView, ...patch } })),
  setExpert: (expertMode) => set({ expertMode }),
  setEnvironment: (environment) => set({ environment }),
  setEngineReady: (ok, err = null) => set({ engineReady: ok, engineError: err }),
  openProjectPayload: (p) => {
    set({ project: p, section: "geometry", runs: [], selectedRunId: null, compareRunIds: [], meshView: {}, logJobId: null });
    events.subscribeProject(p.project_id);
    get().refreshRuns();
  },
  refreshProject: async () => {
    const p = get().project;
    if (!p) return;
    try {
      set({ project: await api.get<ProjectPayload>(`/api/p/${p.project_id}`) });
    } catch (e) {
      notifyError(e);
    }
  },
  patchConfig: async (patch) => {
    const p = get().project;
    if (!p) return false;
    try {
      const res = await api.patch<ProjectPayload>(`/api/p/${p.project_id}/config`, patch);
      // the project may have been closed or switched while the edit was in flight
      if (get().project?.project_id === p.project_id) set({ project: res });
      return true;
    } catch (e) {
      if (e instanceof EngineError && e.status === 409) throw e;
      notifyError(e);
      return false;
    }
  },
  closeProject: () => {
    const p = get().project;
    if (p) api.post(`/api/p/${p.project_id}/close`).catch(() => undefined);
    set({ project: null, runs: [], selectedRunId: null, compareRunIds: [] });
  },
  refreshRuns: async () => {
    const p = get().project;
    if (!p) return;
    try {
      const runs = await api.get<RunSummary[]>(`/api/p/${p.project_id}/runs`);
      const sel = get().selectedRunId;
      set({ runs, selectedRunId: sel ?? runs.find((r) => r.status === "completed")?.id ?? runs[0]?.id ?? null });
    } catch (e) {
      notifyError(e);
    }
  },
  selectRun: (id) => set({ selectedRunId: id }),
  toggleCompare: (id) =>
    set((s) => ({ compareRunIds: s.compareRunIds.includes(id) ? s.compareRunIds.filter((x) => x !== id) : [...s.compareRunIds, id] })),
  setJobs: (jobs) => set({ jobs }),
  setBusy: (key, v) => set((s) => ({ busy: { ...s.busy, [key]: v } })),
  bumpGeometry: () => set((s) => ({ geometryVersion: s.geometryVersion + 1 })),
}));

export function pid(): string {
  const p = useApp.getState().project;
  if (!p) throw new Error("no project open");
  return p.project_id;
}
