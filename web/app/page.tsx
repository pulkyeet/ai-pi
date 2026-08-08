import Link from "next/link";
import { getBenchmarkReports } from "@/lib/api";
import type { BenchmarkReportSummary } from "@/lib/types";

// Statically rendered at build time (phase-13-frontend.md's "Homepage —
// statically rendered"): the ten benchmark reports are fetched once during
// `next build`, baked into HTML, and served with zero runtime dependency on
// the FastAPI backend or Postgres — which is also what makes Supabase's
// 7-day idle-pause risk irrelevant for a logged-out visitor (masterplan
// §12.13 / the README's "Supabase over Neon" note).
export const dynamic = "force-static";

async function loadBenchmarkReports(): Promise<BenchmarkReportSummary[]> {
  try {
    return await getBenchmarkReports();
  } catch {
    // Build-time-only fallback (e.g. the backend isn't deployed yet during
    // an initial build) — a real production build always has these
    // reports available. Never thrown at request time either way, since
    // this page has no client fetch.
    return [];
  }
}

export default async function HomePage() {
  const reports = await loadBenchmarkReports();

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "48px 20px" }}>
      <h1 style={{ fontSize: 28, marginBottom: 8 }}>AI Product Investigator</h1>
      <p style={{ color: "var(--fg-muted)", maxWidth: 640, marginBottom: 32 }}>
        Type a product idea, get an evidence-backed discovery report where every sentence cites a
        verbatim span in a fetched page. Ten benchmark reports below — full drill-down, no login
        required.
      </p>

      <div style={{ display: "flex", gap: 12, marginBottom: 40 }}>
        <Link
          href="/new"
          data-testid="run-your-own"
          style={{
            background: "var(--accent)",
            color: "var(--accent-fg)",
            padding: "10px 18px",
            borderRadius: 8,
            fontWeight: 600,
            fontSize: 14,
            textDecoration: "none",
          }}
        >
          Run your own idea
        </Link>
      </div>

      {reports.length === 0 ? (
        <p style={{ color: "var(--fg-muted)" }}>No benchmark reports published yet.</p>
      ) : (
        <ul
          data-testid="benchmark-list"
          style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}
        >
          {reports.map((r) => (
            <li key={r.run_id}>
              <Link
                href={`/r/${r.run_id}`}
                style={{
                  display: "block",
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  padding: 16,
                  textDecoration: "none",
                  color: "inherit",
                }}
              >
                <div style={{ fontWeight: 600 }}>{r.query}</div>
                <div style={{ fontSize: 13, color: "var(--fg-muted)", marginTop: 4 }}>
                  {r.report.competitors.length} competitors · coverage{" "}
                  {Math.round(r.report.coverage.score * 100)}%
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
