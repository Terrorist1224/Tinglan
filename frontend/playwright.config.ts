import { defineConfig } from "@playwright/test";
import path from "node:path";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 45000,
  webServer: {
    command: path.resolve("../.venv/Scripts/python.exe") + " -m uvicorn backend.main:app --port 8766",
    url: "http://127.0.0.1:8766/api/system",
    reuseExistingServer: true,
    cwd: path.resolve(".."),
    env: {
      ...process.env,
      TINGLAN_DATA: path.resolve("../.tmp/browser-data"),
    },
  },
  use: {
    baseURL: "http://127.0.0.1:8766",
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  outputDir: "../.tmp/e2e",
  reporter: [
    ["list"],
    ["html", { outputFolder: "../.tmp/e2e-report", open: "never" }],
  ],
});
