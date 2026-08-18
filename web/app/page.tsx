import Link from "next/link";
import { getBenchmarkReports } from "@/lib/api";
import type { BenchmarkReportSummary } from "@/lib/types";

export const dynamic = "force-static";

async function loadBenchmarkReports(): Promise<BenchmarkReportSummary[]> {
  try { return await getBenchmarkReports(); } catch { return []; }
}

export default async function HomePage() {
  const reports = await loadBenchmarkReports();
  return (
    <main className="page-shell">
      <section className="hero-grid">
        <div className="hero-copy">
          <div className="eyebrow">Evidence, not guesswork</div>
          <h1 className="hero-title">Know what to build.<br /><em>Prove why.</em></h1>
          <p>Turn an early product idea into a cited market read. Every conclusion opens to the exact words that support it.</p>
          <Link href="/new" data-testid="run-your-own" className="button-primary">Investigate an idea</Link>
        </div>
        <aside className="proof-card" aria-label="Example source evidence">
          <div className="proof-topline"><span>Evidence trace</span><span>01 / 01</span></div>
          <div className="proof-body"><div className="eyebrow">Verbatim source</div><p className="proof-quote">&quot;Categorising receipts by hand is tedious.&quot;</p><div className="proof-meta"><span className="grade-dot">Grade A</span><span>Click any finding to inspect its source.</span></div></div>
        </aside>
      </section>
      <section aria-labelledby="benchmark-heading">
        <div className="section-head"><div><div className="eyebrow">Public benchmarks</div><h2 id="benchmark-heading">Research you can inspect</h2></div><p>Open any published investigation to audit the market signals, source by source.</p></div>
        {reports.length === 0 ? <p className="empty-state">No benchmark reports are published yet.</p> : (
          <div className="report-grid" data-testid="benchmark-list">
            {reports.map((r, index) => <Link href={`/r/${r.run_id}`} className="report-card" key={r.run_id}><span className="report-card-index">REPORT {String(index + 1).padStart(2, "0")}</span><h3>{r.query}</h3><footer>{r.report.competitors.length} competitors · {Math.round(r.report.coverage.score * 100)}% coverage</footer></Link>)}
          </div>
        )}
      </section>
    </main>
  );
}
