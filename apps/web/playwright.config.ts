import { defineConfig } from "@playwright/test";

const baseURL = process.env.LKP_E2E_BASE_URL ?? "http://127.0.0.1:3010";
const webCommand = process.env.LKP_E2E_WEB_COMMAND ?? "pnpm dev";

export default defineConfig({
  testDir: "../../tests/e2e",
  use: { baseURL, trace: "retain-on-failure" },
  webServer: {
    command: webCommand,
    url: baseURL,
    reuseExistingServer: true,
  },
});
