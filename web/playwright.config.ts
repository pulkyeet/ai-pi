import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "on-first-retry",
  },
  // "mobile-chrome" rather than WebKit/`devices["iPhone 14"]`: this sandbox
  // has no root to install WebKit's system shared libraries
  // (`playwright install webkit` fails on `validateHostRequirements`
  // without sudo), and Chromium's mobile-viewport emulation still
  // exercises everything the mobile mitigation actually depends on (CSS
  // viewport width, not a specific rendering engine). Swap to a real
  // WebKit project in an environment where `--with-deps` can run.
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] } },
  ],
  // Two servers, started in order (Playwright awaits each `url` before
  // starting the next): the mock API first, since `next build`'s
  // server-side render of the static homepage needs it reachable *during
  // the build*, then the Next.js app itself.
  webServer: [
    {
      command: "node tests/e2e/mock-server.ts",
      url: "http://127.0.0.1:8899/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run build && npm run start -- -p 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8899",
        NEXT_PUBLIC_SUPABASE_URL: "https://test-project.supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "test-anon-key",
      },
    },
  ],
});
