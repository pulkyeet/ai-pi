import Link from "next/link";
import CountUp from "@/components/CountUp";
import Reveal from "@/components/Reveal";
import TerminalDemo from "@/components/TerminalDemo";
import { getBenchmarkReports } from "@/lib/api";
import type { BenchmarkReportSummary } from "@/lib/types";

export const dynamic = "force-static";

async function loadBenchmarkReports(): Promise<BenchmarkReportSummary[]> {
  try {
    return await getBenchmarkReports();
  } catch {
    return [];
  }
}

const SOURCES = [
  "hn: threads",
  "gh: stars",
  "web: pages",
  "so: answers",
  "grade A sources",
  "verbatim binding",
  "$0.06 avg run",
  "64 sources / report",
];

const PIPELINE = [
  {
    num: "01",
    title: "Search",
    desc: "Query the public web — Hacker News, GitHub, Stack Exchange and more — for signals on your idea.",
  },
  {
    num: "02",
    title: "Retrieve",
    desc: "Fetch candidate pages and keep the raw text, word for word, for later proof.",
  },
  {
    num: "03",
    title: "Extract",
    desc: "Structure competitors, pricing, pain points and feature gaps out of the fetched evidence.",
  },
  {
    num: "04",
    title: "Bind",
    desc: "Every claim is attached to a span found verbatim inside a fetched page. No quote, no claim.",
  },
  {
    num: "05",
    title: "Verify",
    desc: "Grade sources A–D and weigh contradictory signals by recency and source quality.",
  },
  {
    num: "06",
    title: "Report",
    desc: "A cited discovery report you can audit claim by claim, source by source.",
  },
];

export default async function HomePage() {
  const reports = await loadBenchmarkReports();
  return (
    <main>
      <section className="page-shell">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="eyebrow">Evidence, not guesswork</div>
            <h1 className="hero-title">
              Know what to build.
              <br />
              <em>Prove why.</em>
            </h1>
            <p className="hero-sub">
              Turn an early product idea into a cited market read. Every
              conclusion opens to the exact words that support it — quoted
              verbatim from a fetched page.
            </p>
            <div className="hero-cta">
              <Link
                href="/new"
                data-testid="run-your-own"
                className="button-primary"
              >
                Investigate an idea
              </Link>
              <a href="#benchmarks" className="button-secondary">
                Inspect published reports
              </a>
            </div>
            <p className="hero-note">
              $0.06 avg / run · 64 sources / report · claims bound verbatim
            </p>
          </div>
          <TerminalDemo />
        </div>
      </section>

      <section className="page-shell grid-bg" aria-hidden="true">
        <div className="marquee">
          <div className="marquee-track">
            {[0, 1].map((copy) => (
              <div
                className="marquee-track-inner"
                key={copy}
                aria-hidden={copy === 1}
              >
                {SOURCES.map((s) => (
                  <span className="marquee-item" key={s}>
                    <span className="src">◆</span> {s}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="page-shell" aria-label="Measured results">
        <div className="stat-band">
          <div className="stat">
            <div className="stat-num">
              <CountUp value={64} />
            </div>
            <div className="stat-label">Sources fetched / report</div>
          </div>
          <div className="stat">
            <div className="stat-num">
              <CountUp value={128} suffix="s" />
            </div>
            <div className="stat-label">Median report time</div>
          </div>
          <div className="stat">
            <div className="stat-num">
              <CountUp value={100} suffix="%" />
            </div>
            <div className="stat-label">Claims bound verbatim</div>
          </div>
          <div className="stat">
            <div className="stat-num">
              <CountUp value={0.06} prefix="$" decimals={2} />
            </div>
            <div className="stat-label">Avg cost per run</div>
          </div>
        </div>
      </section>

      <section className="page-shell" aria-labelledby="pipeline-heading">
        <div className="section-head">
          <div>
            <div className="eyebrow">How it works</div>
            <h2 id="pipeline-heading">Raw signal, clean insight</h2>
          </div>
          <p>
            The investigation is a real pipeline, and every stage leaves a
            trace you can open.
          </p>
        </div>
        <div className="pipeline-grid">
          {PIPELINE.map((step, index) => (
            <Reveal delay={index * 70} key={step.num}>
              <div className="pipeline-step">
                <div className="step-num">/{step.num}</div>
                <div className="step-title">{step.title}</div>
                <p className="step-desc">{step.desc}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      <section
        className="page-shell"
        id="benchmarks"
        aria-labelledby="benchmark-heading"
      >
        <div className="section-head">
          <div>
            <div className="eyebrow">Public benchmarks</div>
            <h2 id="benchmark-heading">Research you can inspect</h2>
          </div>
          <p>
            Open any published investigation to audit the market signals,
            source by source.
          </p>
        </div>
        {reports.length === 0 ? (
          <p className="empty-state">
            No benchmark reports are published yet.
          </p>
        ) : (
          <div className="report-grid" data-testid="benchmark-list">
            {reports.map((r, index) => (
              <Link
                href={`/r/${r.run_id}`}
                className="report-card"
                key={r.run_id}
              >
                <span className="report-card-index">
                  REPORT {String(index + 1).padStart(2, "0")}
                </span>
                <h3>{r.query}</h3>
                <div className="coverage-bar" aria-hidden="true">
                  <i
                    style={{ width: `${Math.round(r.report.coverage.score * 100)}%` }}
                  />
                </div>
                <footer>
                  {r.report.competitors.length} competitors ·{" "}
                  {Math.round(r.report.coverage.score * 100)}% coverage
                </footer>
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
