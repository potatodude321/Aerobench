import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The renderer is loaded from file:// in production (Electron), so use relative asset paths.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173, strictPort: true },
  build: {
    outDir: "dist",
    sourcemap: true,
    chunkSizeWarningLimit: 4000,
  },
});
