import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests against the live dev container.
 *
 * These run serially: every spec drives the same Home Assistant instance and
 * mutates shared, persisted state (a child's point balance), so parallel
 * workers would race each other.
 */
export default defineConfig({
  testDir: "./specs",
  globalSetup: "./global-setup.ts",

  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,

  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  timeout: 60_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: process.env.HASS_URL ?? "http://localhost:8123",
    storageState: "./.auth/state.json",
    // Must match the container's TZ in docker-compose.dev.yml. Both the cards
    // and the sensors compute "today" in Home Assistant's timezone, so a
    // mismatch flips rows between completed and not-completed near midnight.
    timezoneId: "America/Los_Angeles",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
