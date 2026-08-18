"use client";

import { useState } from "react";
import type { Report } from "@/lib/types";
import { CitedFinding, CitedSentence } from "./CitedSentence";
import { ContradictionCard } from "./ContradictionCard";
import { CoverageBanner } from "./CoverageBanner";
import { SourcePanel } from "./SourcePanel";

export interface ReportViewProps { report: Report; accessToken?: string; showExport?: boolean; }

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

  return <div className="report-view">
    <header className="report-header">
      <div><div className="eyebrow">Evidence report</div><h1>{report.query}</h1><p className="report-context">{report.brief.category} · {report.brief.segment} · {report.brief.geography}</p></div>
      {showExport && <div className="report-actions"><button className="button-secondary" type="button" onClick={exportJson} data-testid="export-json">Export JSON</button><button className="button-secondary" type="button" onClick={() => navigator.clipboard?.writeText(window.location.href)} data-testid="copy-permalink">Copy link</button></div>}
      <div><CoverageBanner coverage={report.coverage} /><p className="report-note" data-testid="freshness-line">Median source age: {report.freshness.median_source_age_days} days · Oldest: {report.freshness.oldest}</p></div>
    </header>

    <section className="report-section"><h2>Competitors</h2><div className="competitor-grid">{report.competitors.map((c) => <article key={c.entity_key} className="data-card"><div className="data-card-title">{c.display_name}</div><div className="data-card-subtitle">{c.maturity ?? "maturity unknown"}</div><CitedSentence claimIds={c.claim_ids} onOpen={setOpenClaimId}><span>{c.positioning}</span></CitedSentence><div className="report-note" style={{ marginTop: 10 }}>{c.pricing.model === "free" ? "Free" : `$${c.pricing.entry_usd_month}/mo · ${c.pricing.model}${c.pricing.free_tier ? " · free tier" : ""}`}</div></article>)}</div></section>
    <section className="report-section"><h2>Pricing landscape</h2><div className="metric-band"><strong>${report.pricing_landscape.median_entry_usd_month}/mo</strong><CitedSentence claimIds={report.pricing_landscape.claim_ids} onOpen={setOpenClaimId}><span>Median entry price, from ${report.pricing_landscape.spread[0]} to ${report.pricing_landscape.spread[1]}.</span></CitedSentence></div></section>
    {report.pain_points.length > 0 && <section className="report-section"><h2>Pain points</h2>{report.pain_points.map((p) => <div key={p.theme} className="data-card"><CitedSentence claimIds={p.claim_ids} onOpen={setOpenClaimId}><span>{p.theme}</span></CitedSentence><div className="data-card-subtitle" style={{ marginTop: 8 }}>{p.support_count} mentions across {p.distinct_threads} threads · grade {p.grade} · confidence {p.confidence.toFixed(2)}</div></div>)}</section>}
    {report.feature_gaps.length > 0 && <section className="report-section"><h2>Feature gaps</h2><ul className="report-list">{report.feature_gaps.map((g, i) => <li key={i}>{g.addresses_finding_ids.map((fid, j) => <span key={fid}>{j > 0 && " "}<CitedFinding findingId={fid} runId={report.run_id} accessToken={accessToken} onOpen={setOpenClaimId}>{j === 0 ? g.statement : `[${fid}]`}</CitedFinding></span>)}</li>)}</ul></section>}
    {report.contradictions.length > 0 && <section className="report-section"><h2>Contradictions</h2>{report.contradictions.map((c, i) => <ContradictionCard key={i} contradiction={c} />)}</section>}
    <section className="report-section"><h2>MVP</h2><div className="metric-band"><CitedFinding findingId={report.mvp.addresses_finding_ids[0] ?? -1} runId={report.run_id} accessToken={accessToken} onOpen={setOpenClaimId}>{report.mvp.statement}</CitedFinding></div></section>
    {report.risks.length > 0 && <section className="report-section"><h2>Risks</h2><ul className="report-list">{report.risks.map((r, i) => <li key={i}><CitedFinding findingId={r.addresses_finding_ids[0] ?? -1} runId={report.run_id} accessToken={accessToken} onOpen={setOpenClaimId}>{r.statement}</CitedFinding></li>)}</ul></section>}
    <footer className="report-footer">${report.meta.cost_usd.toFixed(3)} · {report.meta.duration_s.toFixed(0)}s · {report.meta.sources_fetched} sources · {Math.round(report.meta.cache_hit_rate * 100)}% cache hit</footer>
    {openClaimId !== null && <SourcePanel key={openClaimId} runId={report.run_id} claimId={openClaimId} accessToken={accessToken} onClose={() => setOpenClaimId(null)} onNavigateClaim={setOpenClaimId} />}
  </div>;
}
