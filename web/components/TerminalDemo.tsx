"use client";

import { useEffect, useMemo, useState } from "react";
import Asciify from "@/components/canvasui/Asciify";
import { FlameWrap } from "@/components/canvasui/FlameWrap";

const CMD = 'pi investigate "AI expense tracker for freelancers"';
const SNIPPET =
  "Acme is a leading expense tool for freelancers.\nStarts at $29/mo today. Trusted by many teams.";

const SRC_LINES = [
  "hn:news.ycombinator.com  ·  23 threads",
  "gh:acme/expense-tracker  ·  1.2k stars",
  "web:acme-expense.com/pricing  ·  40 pages",
];

type Phase = "idle" | "typing" | "sources" | "raw" | "bind" | "ready";

export default function TerminalDemo() {
  const reduced = useMemo(
    () =>
      typeof window === "undefined"
        ? false
        : window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );
  const [phase, setPhase] = useState<Phase>("idle");
  const [typed, setTyped] = useState("");
  const [srcCount, setSrcCount] = useState(0);

  useEffect(() => {
    if (reduced) {
      const t = window.setTimeout(() => {
        setTyped(CMD);
        setSrcCount(3);
        setPhase("ready");
      }, 0);
      return () => window.clearTimeout(t);
    }
    if (phase === "idle") {
      const t = window.setTimeout(() => setPhase("typing"), 500);
      return () => window.clearTimeout(t);
    }
  }, [phase, reduced]);

  useEffect(() => {
    if (phase !== "typing") return;
    if (typed.length >= CMD.length) {
      const t = window.setTimeout(() => setPhase("sources"), 450);
      return () => window.clearTimeout(t);
    }
    const t = window.setTimeout(
      () => setTyped(CMD.slice(0, typed.length + 1)),
      26,
    );
    return () => window.clearTimeout(t);
  }, [phase, typed]);

  useEffect(() => {
    if (phase !== "sources") return;
    if (srcCount < SRC_LINES.length) {
      const t = window.setTimeout(() => setSrcCount(srcCount + 1), 520);
      return () => window.clearTimeout(t);
    }
    const t = window.setTimeout(() => setPhase("raw"), 900);
    return () => window.clearTimeout(t);
  }, [phase, srcCount]);

  useEffect(() => {
    if (phase === "raw") {
      const t = window.setTimeout(() => setPhase("bind"), 1500);
      return () => window.clearTimeout(t);
    }
    if (phase === "bind") {
      const t = window.setTimeout(() => setPhase("ready"), 1150);
      return () => window.clearTimeout(t);
    }
  }, [phase]);

  const showRaw = phase === "raw" || phase === "bind" || phase === "ready";
  const showBind = phase === "bind" || phase === "ready";
  const showReport = phase === "ready";

  return (
    <div className="terminal">
      <div className="terminal-bar">
        <div className="terminal-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <span className="terminal-title">product-investigator — zsh</span>
        <div className="terminal-tabs" aria-hidden="true">
          <span className="terminal-tab active">investigate</span>
          <span className="terminal-tab">bind</span>
          <span className="terminal-tab">report</span>
        </div>
      </div>
      <div className="terminal-body">
        <div className="terminal-line">
          <span className="terminal-prompt">$</span> {typed}
          {phase === "typing" && <span className="terminal-cursor" aria-hidden="true" />}
        </div>
        {srcCount >= 1 && (
          <div className="terminal-line terminal-ok">✓ {SRC_LINES[0]}</div>
        )}
        {srcCount >= 2 && (
          <div className="terminal-line terminal-ok">✓ {SRC_LINES[1]}</div>
        )}
        {srcCount >= 3 && (
          <div className="terminal-line terminal-ok">✓ {SRC_LINES[2]}</div>
        )}
        {showRaw && (
          <Asciify
            baseStrength={0.85}
            radius={0.4}
            scale={2}
            spacing={1}
            glow={0.85}
            background="auto"
          >
            <div className="raw-source">{SNIPPET}</div>
          </Asciify>
        )}
        {showBind && (
          <div className="terminal-line">
            <span className="terminal-prompt">bind</span>
            <span className="terminal-hl">&quot;Starts at $29/mo today&quot;</span>{" "}
            <span className="terminal-dim">→</span> pricing.entry_usd_month{" "}
            <span className="terminal-ok">grade A</span>
          </div>
        )}
        {showReport && (
          <FlameWrap
            height={54}
            intensity={0.75}
            spread={26}
            radius={12}
            color={[1, 0.36, 0.1]}
            sparks={0.55}
            smoke={0.15}
          >
            <div className="report-card-demo">
              <span className="dot" aria-hidden="true" />
              <div>
                <b>Report ready</b>
                <span>6 competitors · 82% coverage · 64 sources</span>
              </div>
            </div>
          </FlameWrap>
        )}
        {!showReport && (
          <div className="terminal-line terminal-dim">
            <span className="terminal-cursor" aria-hidden="true" />
          </div>
        )}
      </div>
    </div>
  );
}
