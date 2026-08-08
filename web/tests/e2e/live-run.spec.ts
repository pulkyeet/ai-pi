import { expect, test } from "@playwright/test";
import { installApiMocks, REPORT, RUN_ID } from "./fixtures";

// Seeds a Supabase session directly into the storage key `@supabase/
// supabase-js` v2 reads on init (`sb-<project-ref>-auth-token`, `project-
// ref` being the subdomain of `NEXT_PUBLIC_SUPABASE_URL` — "test-project"
// for the fake URL `playwright.config.ts`'s `webServer` sets). There is no
// real Supabase project in this environment to run an actual Google/GitHub
// OAuth redirect against (no live Postgres or Supabase project is
// reachable here — see docs/tracker.md's Phase 13 entry), so this is the
// closest honest substitute: it exercises the real `getSession()` code
// path `lib/supabase.ts` calls, just with a session that was planted
// instead of one a real OAuth round trip produced.
async function signInAs(page: import("@playwright/test").Page, accessToken: string) {
  await page.addInitScript((token: string) => {
    const session = {
      access_token: token,
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      refresh_token: "test-refresh-token",
      user: {
        id: "00000000-0000-0000-0000-000000000001",
        email: "test@example.com",
        app_metadata: {},
        user_metadata: {},
        aud: "authenticated",
        created_at: new Date().toISOString(),
      },
    };
    window.localStorage.setItem("sb-test-project-auth-token", JSON.stringify(session));
  }, accessToken);
}

test("signed-in visitor: submit a query, watch the checklist tick, see the report render", async ({
  page,
}) => {
  await signInAs(page, "test-access-token");
  // No `sseScript` here — this test deliberately hits `mock-server.ts`'s
  // real, paced `/events` stream (see that file's `streamLiveRunEvents`)
  // instead of `page.route`'s instant single-chunk delivery, because this
  // is the one test that needs to *observe* the checklist mid-run rather
  // than just its end state.
  await installApiMocks(page, { runStatus: "running" });

  await page.goto("/new");
  await expect(page.getByTestId("query-input")).toBeVisible();
  await page.getByTestId("query-input").fill(REPORT.query);
  await page.getByTestId("submit-query").click();

  await expect(page.getByTestId("plan-checklist")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Discover competitors")).toBeVisible();
  await expect(page.getByTestId("finding-item")).toContainText(REPORT.mvp.statement, { timeout: 10_000 });
  await expect(page.getByTestId("checklist-item-done")).toBeVisible({ timeout: 10_000 });

  await expect(page.getByRole("heading", { name: REPORT.query })).toBeVisible({ timeout: 10_000 });
});

function sseEvent(id: number, type: string, data: unknown): string {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("disambiguation chips are ignorable: pressing Go untouched still resumes the run", async ({
  page,
}) => {
  await signInAs(page, "test-access-token");
  await installApiMocks(page, { runStatus: "needs_input", sseScript: [sseEvent(1, "report.ready", { type: "report.ready", run_id: RUN_ID })] });

  await page.goto("/new");
  await page.getByTestId("query-input").fill(REPORT.query);
  await page.getByTestId("submit-query").click();

  await expect(page.getByTestId("disambiguation-chips")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/B2B, freelancers/)).toBeVisible();
  await page.getByTestId("disambiguation-go").click();

  // The run resumes without the visitor having edited anything — the
  // request body sent to PATCH /runs/{id} should carry no overrides.
  await expect(page.getByTestId("plan-checklist").or(page.getByRole("heading", { name: REPORT.query }))).toBeVisible({
    timeout: 10_000,
  });
});
