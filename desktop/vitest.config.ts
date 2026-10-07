import { defineConfig } from "vitest/config";

// Unit tests live next to the code; tests/e2e is run by Playwright (npm run e2e).
export default defineConfig({
  test: { include: ["src/**/*.test.{ts,tsx}"], environment: "node" },
});
