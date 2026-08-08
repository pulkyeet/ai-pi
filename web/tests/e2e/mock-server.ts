// A real (not `page.route`-mocked) HTTP server standing in for the FastAPI
// backend, used only to answer `next build`'s server-side fetch for the
// statically-rendered homepage (`app/page.tsx`'s `getBenchmarkReports()`
// call runs inside the Node build process, which `page.route` — a browser
// network hook — can never intercept). Every other endpoint a test needs
// per-scenario control over (run creation, polling, SSE) is still mocked
// via `page.route` in `fixtures.ts`; this server only needs to serve
// `GET /reports/benchmark` for the build to bake in real data, but answers
// the same fixed-fixture data on the other read endpoints too so a
// `webServer`-only smoke check (no `page.route` involved at all) still
// gets a sane response.
import { createServer } from "node:http";
import { BENCHMARK_LIST, CLAIMS, FINDINGS, REPORT, RUN_ID } from "./data.ts";

const port = Number(process.env.MOCK_API_PORT ?? 8899);

function send(res: import("node:http").ServerResponse, status: number, body: unknown) {
  res.writeHead(status, {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
  });
  res.end(JSON.stringify(body));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// A real, paced SSE stream (unlike `fixtures.ts`'s `page.route`-mocked
// version, which delivers a whole scripted body in one instant chunk) —
// used by the one E2E test that actually needs to *observe* the checklist
// mid-run rather than just its end state. A single instant chunk collapses
// `plan.created` -> `report.ready` into the same browser tick, which is a
// timing artifact of the mock, not of the real pipeline (a real run's
// events are seconds apart) — so this route writes each event with a
// deliberate delay between them, the same way the real backend's own pace
// would.
const LIVE_RUN_EVENTS: { type: string; data: Record<string, unknown> }[] = [
  {
    type: "plan.created",
    data: {
      type: "plan.created",
      run_id: RUN_ID,
      plan: {
        nodes: [{ id: "t1", kind: "discover_competitors", args: {}, budget_weight: 3 }],
        edges: [],
        total_budget_weight: 3,
      },
    },
  },
  { type: "task.started", data: { type: "task.started", run_id: RUN_ID, task_id: 501, kind: "discover_competitors" } },
  {
    type: "finding.added",
    data: { type: "finding.added", run_id: RUN_ID, finding_id: 201, kind: "mvp", statement: REPORT.mvp.statement },
  },
  {
    type: "task.completed",
    data: {
      type: "task.completed",
      run_id: RUN_ID,
      task_id: 501,
      kind: "discover_competitors",
      cost_usd: 0.01,
      latency_ms: 300,
    },
  },
  { type: "report.ready", data: { type: "report.ready", run_id: RUN_ID } },
];

async function streamLiveRunEvents(res: import("node:http").ServerResponse, sinceId: number) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "access-control-allow-origin": "*",
    "cache-control": "no-cache",
  });
  for (let i = 0; i < LIVE_RUN_EVENTS.length; i++) {
    const id = i + 1;
    if (id <= sinceId) continue;
    await sleep(250);
    const event = LIVE_RUN_EVENTS[i]!;
    res.write(`id: ${id}\nevent: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`);
  }
  res.end();
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);

  if (url.pathname === "/health") {
    return send(res, 200, { status: "ok", kill_switch_enabled: false, kill_switch_reason: null });
  }
  if (url.pathname === "/reports/benchmark") {
    return send(res, 200, BENCHMARK_LIST);
  }
  if (url.pathname === `/runs/${RUN_ID}/report.json`) {
    return send(res, 200, REPORT);
  }
  const claimMatch = url.pathname.match(new RegExp(`^/runs/${RUN_ID}/claims/(\\d+)$`));
  if (claimMatch) {
    const claim = CLAIMS[Number(claimMatch[1])];
    return claim
      ? send(res, 200, claim)
      : send(res, 404, { error: { code: "not_found", message: "no such claim", correlation_id: "x" } });
  }
  const findingMatch = url.pathname.match(new RegExp(`^/runs/${RUN_ID}/findings/(\\d+)$`));
  if (findingMatch) {
    const finding = FINDINGS[Number(findingMatch[1])];
    return finding
      ? send(res, 200, finding)
      : send(res, 404, { error: { code: "not_found", message: "no such finding", correlation_id: "x" } });
  }
  if (url.pathname === `/runs/${RUN_ID}/events`) {
    const lastEventId = req.headers["last-event-id"];
    const sinceId = typeof lastEventId === "string" ? Number(lastEventId) : 0;
    void streamLiveRunEvents(res, Number.isFinite(sinceId) ? sinceId : 0);
    return;
  }
  return send(res, 404, { error: { code: "not_found", message: "no such route", correlation_id: "x" } });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`mock API listening on http://127.0.0.1:${port}`);
});
