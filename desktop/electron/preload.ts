/** Minimal, explicit bridge between the sandboxed renderer and the main process. */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("aerobench", {
  isDesktop: true,
  platform: process.platform,
  getEngine: () => ipcRenderer.invoke("engine:info"),
  engineLog: () => ipcRenderer.invoke("engine:log"),
  openFile: (opts?: { title?: string; filters?: { name: string; extensions: string[] }[] }) => ipcRenderer.invoke("dialog:openFile", opts),
  openFiles: (opts?: { title?: string; filters?: { name: string; extensions: string[] }[] }) => ipcRenderer.invoke("dialog:openFiles", opts),
  openDirectory: (opts?: { title?: string; create?: boolean }) => ipcRenderer.invoke("dialog:openDirectory", opts),
  saveFile: (opts?: { title?: string; defaultPath?: string; filters?: { name: string; extensions: string[] }[] }) =>
    ipcRenderer.invoke("dialog:saveFile", opts),
  openPath: (p: string) => ipcRenderer.invoke("shell:openPath", p),
  showItem: (p: string) => ipcRenderer.invoke("shell:showItem", p),
  openParaView: (exe: string, file: string) => ipcRenderer.invoke("paraview:open", exe, file),
  printToPDF: (url: string, outPath: string) => ipcRenderer.invoke("pdf:fromUrl", url, outPath),
  versions: () => ipcRenderer.invoke("app:versions"),
  installMsmpi: () => ipcRenderer.invoke("msmpi:install"),
  onEngineExit: (cb: (info: { code: number; log: string[] }) => void) => ipcRenderer.on("engine:exited", (_e, info) => cb(info)),
});
