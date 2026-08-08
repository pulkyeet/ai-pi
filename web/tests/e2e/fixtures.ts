import type { Page, Route } from "@playwright/test";
import { BENCHMARK_LIST, CLAIMS, FINDINGS, REPORT, RUN_ID } from "./data";

export { BENCHMARK_LIST, REPORT, RUN_ID };

// `NEXT_PUBLIC_API_BASE_URL` (playwright.config.ts's `webServer` env) points
// at `mock-server.ts`'s real address — a plain Node http server, not
// `page.route`. That split is deliberate: the homepage is statically
// rendered at `next build` time (phase-13-frontend.md), and that fetch runs
// server-side inside the build process, which `page.route` (a browser
// network hook) can never see. `mock-server.ts` gives the static build
// something real to fetch from; `page.route` here overrides the *browser's*
// requests for every page that fetches client-side (everything except the
// homepage's benchmark list), which is what lets each test parametrise run
// status / SSE scripts per-test without restarting the mock server.
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export interface MockOptions {
  runStatus?: "pending" | "needs_input" | "running" | "done" | "failed";
  sseScript?: string[];
}

export async function installApiMocks(page: Page, options: MockOptions = {}) {
  await page.route("**/health", (route) =>
    json(route, { status: "ok", kill_switch_enabled: false, kill_switch_reason: null }),
  );

  await page.route("**/reports/benchmark", (route) => json(route, BENCHMARK_LIST));

  await page.route(`**/runs/${RUN_ID}/report.json`, (route) => json(route, REPORT));

  await page.route(`**/runs/${RUN_ID}/claims/*`, (route) => {
    const claimId = Number(route.request().url().split("/").pop());
    const claim = CLAIMS[claimId];
    return claim
      ? json(route, claim)
      : json(route, { error: { code: "not_found", message: "no such claim", correlation_id: "x" } }, 404);
  });

  await page.route(`**/runs/${RUN_ID}/findings/*`, (route) => {
    const findingId = Number(route.request().url().split("/").pop());
    const finding = FINDINGS[findingId];
    return finding
      ? json(route, finding)
      : json(route, { error: { code: "not_found", message: "no such finding", correlation_id: "x" } }, 404);
  });

  // `POST /runs` always answers "pending" for real (src/api/web/routes/
  // runs.py's `create_run` hardcodes it — disambiguation is only ever
  // discovered later, by the caller polling `GET /runs/{id}`). Registered
  // before the `${RUN_ID}` route below so Playwright's most-recently-added-
  // matches-first ordering tries this one first for the exact `/runs` path.
  await page.route(`**/runs`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return json(route, { run_id: RUN_ID, status: "pending", disambiguation_fields: [] }, 202);
  });

  await page.route(`**/runs/${RUN_ID}`, (route) => {
    if (route.request().method() === "PATCH") {
      return json(route, { run_id: RUN_ID, status: "running", disambiguation_fields: [] }, 202);
    }
    if (route.request().method() !== "GET" || !options.runStatus) return route.fallback();
    return json(route, {
      run_id: RUN_ID,
      query: REPORT.query,
      status: options.runStatus,
      cost_usd: null,
      coverage: null,
      brief: REPORT.brief,
      disambiguation_fields: options.runStatus === "needs_input" ? ["segment", "geography"] : [],
      queue_position: 0,
    });
  });

  if (options.sseScript) {
    const body = options.sseScript.join("");
    await page.route(`**/runs/${RUN_ID}/events`, (route) =>
      route.fulfill({ status: 200, contentType: "text/event-stream", body }),
    );
  }
}
