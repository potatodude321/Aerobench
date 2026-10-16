/** Typed access to the Electron preload bridge (absent when running in a plain browser). */
export interface FileFilter {
  name: string;
  extensions: string[];
}

export interface DesktopBridge {
  isDesktop: true;
  platform: string;
  getEngine(): Promise<{ url: string; token: string; pid: number } | null>;
  engineLog(): Promise<string[]>;
  openFile(opts?: { title?: string; filters?: FileFilter[] }): Promise<string | null>;
  openFiles(opts?: { title?: string; filters?: FileFilter[] }): Promise<string[]>;
  openDirectory(opts?: { title?: string; create?: boolean }): Promise<string | null>;
  saveFile(opts?: { title?: string; defaultPath?: string; filters?: FileFilter[] }): Promise<string | null>;
  openPath(p: string): Promise<string>;
  showItem(p: string): Promise<void>;
  openParaView(exe: string, file: string): Promise<{ ok: boolean; error?: string }>;
  printToPDF(url: string, outPath: string): Promise<{ ok: boolean; path: string }>;
  versions(): Promise<Record<string, string>>;
  installMsmpi(): Promise<{ ok: boolean; error?: string }>;
  onEngineExit(cb: (info: { code: number; log: string[] }) => void): void;
}

declare global {
  interface Window {
    aerobench?: DesktopBridge;
  }
}

export const bridge: DesktopBridge | undefined = typeof window !== "undefined" ? window.aerobench : undefined;
export const isDesktop = !!bridge?.isDesktop;
