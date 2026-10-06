import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 15 * 60 * 1000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
});
