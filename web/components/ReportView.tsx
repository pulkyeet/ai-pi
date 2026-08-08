"use client";

import { useState } from "react";
import type { Report } from "@/lib/types";
import { CitedFinding, CitedSentence } from "./CitedSentence";
import { ContradictionCard } from "./ContradictionCard";
import { CoverageBanner } from "./CoverageBanner";
import { SourcePanel } from "./SourcePanel";

export interface ReportViewProps {
  report: Report;
  accessToken?: string;
  showExport?: boolean;
}

const section: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 10 };
const heading: React.CSSProperties = { fontSize: 15, fontWeight: 700, margin: 0 };
const card: React.CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: 14,
};

export function ReportView({ report, accessToken, showExport = true }: ReportViewProps) {
  const [openClaimId, setOpenClaimId] = useState<number | null>(null);

  function exportJson() {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${report.run_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <header style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{report.query}</h1>
        <p style={{ color: "var(--fg-muted)", margin: 0, fontSize: 14 }}>
          {report.brief.category} · {report.brief.segment} · {report.brief.geography}
        </p>
        <CoverageBanner coverage={report.coverage} />
        <p style={{ fontSize: 13, color: "var(--fg-muted)", margin: 0 }} data-testid="freshness-line">
          Median source age: {report.freshness.median_source_age_days} days · Oldest:{" "}
          {report.freshness.oldest}
        </p>
        {showExport && (
          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              onClick={exportJson}
              data-testid="export-json"
              style={{
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "6px 12px",
                background: "none",
                color: "inherit",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Export JSON
            </button>
            <button
              type="button"
              onClick={() => navigator.clipboard?.writeText(window.location.href)}
              data-testid="copy-permalink"
              style={{
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "6px 12px",
                background: "none",
                color: "inherit",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Copy permalink
            </button>
          </div>
        )}
      </header>

      <section style={section}>
        <h2 style={heading}>Competitors</h2>
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
          {report.competitors.map((c) => (
            <div key={c.entity_key} style={card}>
              <div style={{ fontWeight: 600 }}>{c.display_name}</div>
              <div style={{ fontSize: 12, color: "var(--fg-muted)", marginBottom: 6 }}>
                {c.maturity ?? "maturity unknown"}
              </div>
              <CitedSentence claimIds={c.claim_ids} onOpen={setOpenClaimId}>
                <span style={{ fontSize: 13 }}>{c.positioning}</span>
              </CitedSentence>
              <div style={{ fontSize: 13, marginTop: 6 }}>
                ${c.pricing.entry_usd_month}/mo · {c.pricing.model}
                {c.pricing.free_tier ? " · free tier" : ""}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section style={section}>
        <h2 style={heading}>Pricing landscape</h2>
        <CitedSentence claimIds={report.pricing_landscape.claim_ids} onOpen={setOpenClaimId}>
          <span>
            Median entry ${report.pricing_landscape.median_entry_usd_month}/mo, spread [$
            {report.pricing_landscape.spread[0]}–${report.pricing_landscape.spread[1]}]
          </span>
        </CitedSentence>
      </section>

      {report.pain_points.length > 0 && (
        <section style={section}>
          <h2 style={heading}>Pain points</h2>
          {report.pain_points.map((p) => (
            <div key={p.theme} style={card}>
              <CitedSentence claimIds={p.claim_ids} onOpen={setOpenClaimId}>
                <span>{p.theme}</span>
              </CitedSentence>
              <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 4 }}>
                {p.support_count} mentions across {p.distinct_threads} threads · grade {p.grade} ·
                confidence {p.confidence.toFixed(2)}
              </div>
            </div>
          ))}
        </section>
      )}

      {report.feature_gaps.length > 0 && (
        <section style={section}>
          <h2 style={heading}>Feature gaps</h2>
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            {report.feature_gaps.map((g, i) => (
              <li key={i}>
                {g.addresses_finding_ids.map((fid, j) => (
                  <span key={fid}>
                    {j > 0 && " "}
                    <CitedFinding findingId={fid} runId={report.run_id} accessToken={accessToken} onOpen={setOpenClaimId}>
                      {j === 0 ? g.statement : `[${fid}]`}
                    </CitedFinding>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.contradictions.length > 0 && (
        <section style={section}>
          <h2 style={heading}>Contradictions</h2>
          {report.contradictions.map((c, i) => (
            <ContradictionCard key={i} contradiction={c} />
          ))}
        </section>
      )}

      <section style={section}>
        <h2 style={heading}>MVP</h2>
        <CitedFinding
          findingId={report.mvp.addresses_finding_ids[0] ?? -1}
          runId={report.run_id}
          accessToken={accessToken}
          onOpen={setOpenClaimId}
        >
          {report.mvp.statement}
        </CitedFinding>
      </section>

      {report.risks.length > 0 && (
        <section style={section}>
          <h2 style={heading}>Risks</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {report.risks.map((r, i) => (
              <li key={i}>
                <CitedFinding
                  findingId={r.addresses_finding_ids[0] ?? -1}
                  runId={report.run_id}
                  accessToken={accessToken}
                  onOpen={setOpenClaimId}
                >
                  {r.statement}
                </CitedFinding>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer style={{ fontSize: 12, color: "var(--fg-muted)" }}>
        ${report.meta.cost_usd.toFixed(3)} · {report.meta.duration_s.toFixed(0)}s ·{" "}
        {report.meta.sources_fetched} sources · {Math.round(report.meta.cache_hit_rate * 100)}% cache hit
      </footer>

      {openClaimId !== null && (
        <SourcePanel
          key={openClaimId}
          runId={report.run_id}
          claimId={openClaimId}
          accessToken={accessToken}
          onClose={() => setOpenClaimId(null)}
          onNavigateClaim={setOpenClaimId}
        />
      )}
    </div>
  );
}
