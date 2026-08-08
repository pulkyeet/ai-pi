"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, getReport } from "@/lib/api";
import { currentAccessToken } from "@/lib/supabase";
import type { Report } from "@/lib/types";
import { ReportView } from "@/components/ReportView";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; report: Report; accessToken?: string };

// Client-rendered (unlike the homepage): a permalink may point at a
// private run, whose auth is decided by the caller's Supabase session, not
// at build time. Full drill-down still works fully logged out for a public
// benchmark run — `getReport`/`getClaimDrilldown` simply omit the
// `Authorization` header, and the backend's `optional_user` +
// `_authorize_row` (src/api/web/routes/runs.py) allow it.
export default function ReportPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      let accessToken: string | undefined;
      try {
        accessToken = (await currentAccessToken()) ?? undefined;
      } catch {
        accessToken = undefined; // Supabase unconfigured — fine for a public run.
      }
      try {
        const report = await getReport(runId, accessToken);
        if (!cancelled) setState({ status: "ready", report, accessToken });
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "failed to load report";
        setState({ status: "error", message });
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "48px 20px" }}>
      <Link href="/" style={{ fontSize: 13, color: "var(--accent)" }}>
        ← All reports
      </Link>
      <div style={{ marginTop: 20 }}>
        {state.status === "loading" && <p>Loading report…</p>}
        {state.status === "error" && (
          <p role="alert" data-testid="report-error">
            {state.message}
          </p>
        )}
        {state.status === "ready" && (
          <ReportView report={state.report} accessToken={state.accessToken} />
        )}
      </div>
    </main>
  );
}
