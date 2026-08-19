"use client";

import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { ApiError, createRun, eventsUrl, getReport, getRun, resolveDisambiguation } from "@/lib/api";
import { initialChecklist, reduceChecklist, type ChecklistItem } from "@/lib/checklist";
import { streamRunEvents } from "@/lib/sse";
import { supabaseBrowserClient } from "@/lib/supabase";
import type { DisambiguationOverrides, FindingAddedEvent, Report, ResearchBrief } from "@/lib/types";
import { DisambiguationChips } from "@/components/DisambiguationChips";
import { PlanChecklist } from "@/components/PlanChecklist";
import { ReportView } from "@/components/ReportView";

type Phase = "form" | "queued" | "needs_input" | "running" | "done" | "failed" | "error";

const POLL_MS = 1500;

export default function NewRunPage() {
  const [supabaseConfigured] = useState(() => {
    try {
      supabaseBrowserClient();
      return true;
    } catch {
      return false;
    }
  });
  const [session, setSession] = useState<Session | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const authReady = !supabaseConfigured || sessionChecked;
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [runId, setRunId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [brief, setBrief] = useState<ResearchBrief | null>(null);
  const [disambigFields, setDisambigFields] = useState<string[]>([]);
  const [queuePosition, setQueuePosition] = useState<number | null>(null);
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [findings, setFindings] = useState<FindingAddedEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const overridesRef = useRef<DisambiguationOverrides>({});
  const taskIdToNodeId = useRef(new Map<number, string>());

  useEffect(() => {
    if (!supabaseConfigured) return;
    const client = supabaseBrowserClient();
    client.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setSessionChecked(true);
    });
    const { data: sub } = client.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, [supabaseConfigured]);

  // Poll `GET /runs/{id}` while queued, to notice a disambiguation pause
  // (`status='needs_input'`) — the SSE stream never closes for that case,
  // only for `report.ready`/`failed` (src/api/web/sse.py), so polling is
  // what actually detects it.
  useEffect(() => {
    if (phase !== "queued" || !runId) return;
    const accessToken = session?.access_token;
    const interval = setInterval(async () => {
      try {
        const run = await getRun(runId, accessToken);
        setQueuePosition(run.queue_position);
        if (run.status === "needs_input") {
          setBrief(run.brief);
          setDisambigFields(run.disambiguation_fields ?? []);
          setPhase("needs_input");
        } else if (run.status === "running") {
          setPhase("running");
        } else if (run.status === "done") {
          setPhase("done");
        } else if (run.status === "failed") {
          setPhase("failed");
        }
      } catch {
        // Transient poll failure — try again on the next tick.
      }
    }, POLL_MS);
    return () => clearInterval(interval);
  }, [phase, runId, session]);

  useEffect(() => {
    if (phase !== "running" || !runId) return;
    const controller = new AbortController();
    taskIdToNodeId.current = new Map();

    (async () => {
      for await (const { event } of streamRunEvents(eventsUrl(runId), {
        accessToken: session?.access_token,
        signal: controller.signal,
      })) {
        if (event.type === "plan.created") {
          setChecklist(initialChecklist(event.plan));
        } else if (
          event.type === "task.started" ||
          event.type === "task.completed" ||
          event.type === "task.failed"
        ) {
          setChecklist((prev) => reduceChecklist(prev, taskIdToNodeId.current, event));
        } else if (event.type === "finding.added") {
          setFindings((prev) => [...prev, event]);
        } else if (event.type === "report.ready") {
          try {
            const r = await getReport(runId, session?.access_token);
            setReport(r);
            setPhase("done");
          } catch (err) {
            setErrorMessage(err instanceof ApiError ? err.message : "failed to load report");
            setPhase("error");
          }
        }
      }
    })();

    return () => controller.abort();
  }, [phase, runId, session]);

  async function submitQuery() {
    if (!session) return;
    setErrorMessage(null);
    try {
      const accepted = await createRun(query, session.access_token);
      setRunId(accepted.run_id);
      setPhase(accepted.status === "needs_input" ? "needs_input" : "queued");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "failed to create run");
      setPhase("error");
    }
  }

  async function submitDisambiguation() {
    if (!session || !runId) return;
    try {
      await resolveDisambiguation(runId, overridesRef.current, session.access_token);
      setPhase("running");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "failed to resume run");
      setPhase("error");
    }
  }

  async function signIn(provider: "google" | "github") {
    if (!supabaseConfigured) return;
    try {
      await supabaseBrowserClient().auth.signInWithOAuth({
        provider,
        options: { redirectTo: `${window.location.origin}/new` },
      });
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "sign-in failed");
    }
  }

  async function signOut() {
    try {
      await supabaseBrowserClient().auth.signOut();
      setRunId(null);
      setPhase("form");
      setQuery("");
      setReport(null);
      setFindings([]);
      setChecklist([]);
      setBrief(null);
      setDisambigFields([]);
      setQueuePosition(null);
      setErrorMessage(null);
      overridesRef.current = {};
      taskIdToNodeId.current = new Map();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "sign-out failed");
    }
  }

  if (!authReady) return null;

  if (!session) {
    return (
      <main className="workspace">
        <div className="auth-card">
        <div className="eyebrow">Private research</div>
        <h1>Sign in to run an idea</h1>
        <p>
          Logged-out visitors can read every benchmark report, drill-down included. Live runs need
          an account so usage can be quota&#39;d fairly.
        </p>
        {errorMessage && (
          <p role="alert" style={{ color: "var(--red)", margin: "0 0 12px" }}>
            {errorMessage}
          </p>
        )}
        {supabaseConfigured ? (
          <div className="auth-actions">
            <button className="button-secondary" type="button" onClick={() => void signIn("google")} data-testid="sign-in-google">
              Continue with Google
            </button>
            <button className="button-secondary" type="button" onClick={() => void signIn("github")} data-testid="sign-in-github">
              Continue with GitHub
            </button>
          </div>
        ) : (
          <p className="mono" style={{ color: "var(--fg-muted)" }}>
            Sign-in is not configured in this deployment — no OAuth credentials found.
          </p>
        )}
        </div>
      </main>
    );
  }

  return (
    <main className="workspace">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
          gap: 12,
        }}
      >
        <span className="mono" style={{ color: "var(--fg-muted)", fontSize: "0.8rem" }} data-testid="session-email">
          {session.user.email}
        </span>
        <button
          className="button-secondary"
          type="button"
          onClick={() => void signOut()}
          data-testid="sign-out"
        >
          Sign out
        </button>
      </div>
      {phase === "form" && (
        <div>
          <div className="workspace-intro">
            <div className="eyebrow">New investigation</div>
            <h1>What are you building?</h1>
            <p>Describe the product in your own words. We will trace the market evidence back to its source.</p>
          </div>
          <div className="query-panel">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="AI expense tracker for freelancers"
            rows={3}
            data-testid="query-input"
          />
          {errorMessage && (
            <p role="alert" style={{ color: "var(--red)", margin: "0 0 12px" }}>
              {errorMessage}
            </p>
          )}
          <footer><span className="query-hint">Market, audience, or problem space</span><button className="button-primary" type="button" onClick={submitQuery} disabled={query.trim().length === 0} data-testid="submit-query">Start investigation</button></footer>
          </div>
        </div>
      )}

      {phase === "queued" && (
        <div className="run-status"><p data-testid="queue-status">
          Queued{queuePosition !== null && queuePosition > 0 ? ` — position ${queuePosition}` : ""}…
        </p></div>
      )}

      {phase === "needs_input" && brief && (
        <div className="run-status" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p>A couple of guesses — correct them if wrong, or just hit Go.</p>
          <DisambiguationChips
            brief={brief}
            fields={disambigFields}
            onChange={(overrides) => {
              overridesRef.current = overrides;
            }}
          />
          <button className="button-primary" type="button" onClick={submitDisambiguation} data-testid="disambiguation-go">
            Continue
          </button>
        </div>
      )}

      {phase === "running" && (
        <div className="run-status" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <PlanChecklist items={checklist} />
          {findings.length > 0 && (
            <ul className="finding-stream">
              {findings.map((f) => (
                <li key={f.finding_id} data-testid="finding-item">
                  <span className="finding-kind">{f.kind}</span>
                  {f.statement}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {phase === "done" && report && <ReportView report={report} accessToken={session.access_token} />}

      {(phase === "failed" || phase === "error") && (
        <p
          role="alert"
          data-testid="run-error"
          className="mono"
          style={{ color: "var(--red)" }}
        >
          {errorMessage ?? "This run failed. Coverage/partial results, if any, are still on the report."}
        </p>
      )}
    </main>
  );
}
