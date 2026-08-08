"use client";

import { getFindingDrilldown } from "@/lib/api";

export interface CitedSentenceProps {
  children: React.ReactNode;
  claimIds: number[];
  onOpen: (claimId: number) => void;
}

// Every findings sentence carries at least one `claim_id` (masterplan §4.3:
// "findings is the only table whose text ever reaches a user, and it
// always carries claim_ids... that single constraint is the entire
// drill-down mechanism"). Multi-cite sentences open on the first claim and
// show a count — the panel itself offers navigation from there (phase doc's
// Open Decision #1, leaning "first-plus-navigation" over stacking all
// citations at once).
export function CitedSentence({ children, claimIds, onOpen }: CitedSentenceProps) {
  const first = claimIds[0];
  if (first === undefined) return <span>{children}</span>;

  return (
    <span>
      <button
        type="button"
        data-testid="cited-sentence"
        data-claim-ids={claimIds.join(",")}
        onClick={() => onOpen(first)}
        style={{
          border: "none",
          background: "none",
          padding: 0,
          font: "inherit",
          textAlign: "left",
          cursor: "pointer",
          textDecoration: "underline",
          textDecorationStyle: "dotted",
          textUnderlineOffset: 3,
          color: "inherit",
        }}
      >
        {children}
      </button>
      <sup style={{ color: "var(--accent)", fontSize: 11, marginLeft: 2 }}>
        {claimIds.length > 1 ? `[${claimIds.length}]` : "[1]"}
      </sup>
    </span>
  );
}

export interface CitedFindingProps {
  children: React.ReactNode;
  findingId: number;
  runId: string;
  accessToken?: string;
  onOpen: (claimId: number) => void;
}

// `MVP`/`Risk`/`FeatureGap` only carry `addresses_finding_ids` (a
// `findings.id`), not claim ids directly — `GET /runs/{id}/findings/{id}`
// resolves that one extra hop on click, then opens the same `SourcePanel`
// any other cited sentence does.
export function CitedFinding({ children, findingId, runId, accessToken, onOpen }: CitedFindingProps) {
  async function handleClick() {
    const finding = await getFindingDrilldown(runId, findingId, accessToken);
    const first = finding.claim_ids[0];
    if (first !== undefined) onOpen(first);
  }

  return (
    <button
      type="button"
      data-testid="cited-finding"
      data-finding-id={findingId}
      onClick={handleClick}
      style={{
        border: "none",
        background: "none",
        padding: 0,
        font: "inherit",
        textAlign: "left",
        cursor: "pointer",
        textDecoration: "underline",
        textDecorationStyle: "dotted",
        textUnderlineOffset: 3,
        color: "inherit",
      }}
    >
      {children}
    </button>
  );
}
