"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, getClaimDrilldown } from "@/lib/api";
import { breakdownConfidence } from "@/lib/confidence";
import { resolveHighlightSpan } from "@/lib/span";
import type { ClaimDrilldown } from "@/lib/types";
import { SpanHighlight } from "./SpanHighlight";

export interface SourcePanelProps {
  runId: string;
  claimId: number;
  accessToken?: string;
  onClose: () => void;
  onNavigateClaim: (claimId: number) => void;
}

// The demo (phase-13-frontend.md §"Why the drill-down is the whole UI").
// Full-screen sheet below 640px, side panel above it; focus-trapped and
// escapable (a11y exit criterion).
export function SourcePanel({ runId, claimId, accessToken, onClose, onNavigateClaim }: SourcePanelProps) {
  // The parent keys this component on `claimId` (see `ReportView`'s
  // `<SourcePanel key={openClaimId} .../>`), so a claim switch remounts it
  // and this lazy initial value is all "reset to loading" needs — no
  // synchronous `setState` inside the effect below.
  const [state, setState] = useState<
    { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: ClaimDrilldown }
  >({ status: "loading" });
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getClaimDrilldown(runId, claimId, accessToken)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "failed to load source";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [runId, claimId, accessToken]);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        justifyContent: "flex-end",
        zIndex: 50,
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Source"
        tabIndex={-1}
        data-testid="source-panel"
        onClick={(e) => e.stopPropagation()}
        style={{
          color: "var(--fg)",
          width: "min(560px, 100vw)",
          height: "100%",
          overflowY: "auto",
          padding: 20,
        }}
        className="source-dialog"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close source panel"
          style={{ border: "none", background: "none", cursor: "pointer", fontSize: 20, float: "right" }}
        >
          ×
        </button>

        {state.status === "loading" && <p>Loading source…</p>}
        {state.status === "error" && <p role="alert">{state.message}</p>}
        {state.status === "ready" && <SourcePanelBody data={state.data} onNavigateClaim={onNavigateClaim} />}
      </div>
    </div>
  );
}

function SourcePanelBody({
  data,
  onNavigateClaim,
}: {
  data: ClaimDrilldown;
  onNavigateClaim: (claimId: number) => void;
}) {
  const fetchedDate = new Date(data.source_fetched_at).toISOString().slice(0, 10);
  const breakdown = data.confidence_inputs ? breakdownConfidence(data.confidence_inputs) : null;
  const { text: bodyText, charStart, charEnd } = resolveHighlightSpan(data);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 8 }}>
      <div>
        <a
          href={data.source_url}
          target="_blank"
          rel="noreferrer"
          style={{ fontSize: 13, color: "var(--accent)", wordBreak: "break-all" }}
        >
          {data.source_url}
        </a>
        <div style={{ display: "flex", gap: 8, marginTop: 6, fontSize: 12, color: "var(--fg-muted)" }}>
          <span>fetched {fetchedDate}</span>
          <span
            data-testid="grade-badge"
            style={{
              border: "1px solid var(--border)",
              borderRadius: 999,
              padding: "0 6px",
              fontWeight: 600,
            }}
          >
            grade {data.grade}
          </span>
        </div>
      </div>

      <div data-testid="source-text" style={{ background: "var(--bg-subtle)", borderRadius: 8, padding: 12 }}>
        <SpanHighlight text={bodyText} charStart={charStart} charEnd={charEnd} />
      </div>
      {!data.source_text && (
        <p style={{ fontSize: 12, color: "var(--fg-muted)" }}>
          Full page text has been evicted from cache — showing the saved quote context instead.
        </p>
      )}

      {breakdown && (
        <div style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Confidence: {data.confidence.toFixed(2)}</div>
          <code data-testid="confidence-formula" style={{ color: "var(--fg-muted)" }}>
            {data.confidence.toFixed(2)} = {breakdown.formula}
          </code>
        </div>
      )}

      {data.other_claims.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
            Other claims from this source
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {data.other_claims.map((claim) => (
              <li key={claim.claim_id}>
                <button
                  type="button"
                  onClick={() => onNavigateClaim(claim.claim_id)}
                  style={{
                    border: "none",
                    background: "none",
                    color: "var(--accent)",
                    cursor: "pointer",
                    textAlign: "left",
                    padding: 0,
                    fontSize: 13,
                  }}
                >
                  {claim.attribute}: {claim.value_text ?? claim.value_num}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
