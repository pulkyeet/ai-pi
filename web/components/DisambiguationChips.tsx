"use client";

import { useState } from "react";
import type { DisambiguationOverrides, ResearchBrief } from "@/lib/types";

export interface DisambiguationChipsProps {
  brief: ResearchBrief;
  fields: string[];
  onChange: (overrides: DisambiguationOverrides) => void;
}

const FIELD_LABELS: Record<string, string> = {
  category: "Category",
  segment: "Segment",
  geography: "Geography",
  monetisation_guess: "Monetisation",
};

function fieldValue(brief: ResearchBrief, field: string): string {
  return (brief as unknown as Record<string, string>)[field] ?? "";
}

// At most two chips (`select_disambiguation_fields`,
// `MAX_DISAMBIGUATION_CHIPS = 2`, src/api/planner/interpret.py), best guess
// pre-selected. The interpreter reports *which* fields are low-confidence
// but not a closed set of alternatives (`geography`/`segment` are free
// text, not enums) — so each chip edits the guess inline instead of
// picking from invented options, and stays a no-op on the overrides object
// until the visitor actually types something. Pressing "Go" untouched
// sends no overrides at all, which is exactly "ignorable" (masterplan §3).
export function DisambiguationChips({ brief, fields, onChange }: DisambiguationChipsProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<DisambiguationOverrides>({});

  function commit(field: string, value: string) {
    const next = { ...overrides, [field]: value } as DisambiguationOverrides;
    setOverrides(next);
    onChange(next);
    setEditing(null);
  }

  if (fields.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }} data-testid="disambiguation-chips">
      {fields.slice(0, 2).map((field) => {
        const current =
          (overrides as Record<string, string | undefined>)[field] ?? fieldValue(brief, field);
        const isEditing = editing === field;
        return (
          <div
            key={field}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              border: "1px solid var(--border)",
              borderRadius: 999,
              padding: "4px 10px",
              fontSize: 13,
            }}
          >
            <span style={{ color: "var(--fg-muted)" }}>{FIELD_LABELS[field] ?? field}:</span>
            {isEditing ? (
              <input
                autoFocus
                defaultValue={current}
                aria-label={`Edit ${FIELD_LABELS[field] ?? field}`}
                onBlur={(e) => commit(field, e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commit(field, e.currentTarget.value);
                  if (e.key === "Escape") setEditing(null);
                }}
                style={{ border: "none", outline: "none", background: "transparent", width: 140 }}
              />
            ) : (
              <button
                type="button"
                onClick={() => setEditing(field)}
                style={{
                  border: "none",
                  background: "none",
                  cursor: "pointer",
                  fontWeight: 600,
                  color: "var(--accent)",
                  padding: 0,
                }}
              >
                {current} ✓
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
