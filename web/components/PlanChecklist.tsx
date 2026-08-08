import type { ChecklistItem } from "@/lib/checklist";

const KIND_LABELS: Record<string, string> = {
  discover_competitors: "Discover competitors",
  profile_product: "Profile product",
  extract_pricing: "Extract pricing",
  mine_community: "Mine community",
  oss_profile: "Profile OSS repo",
  find_funding: "Find funding",
  trend_signals: "Trend signals",
};

const STATUS_STYLE: Record<ChecklistItem["status"], { icon: string; color: string }> = {
  pending: { icon: "○", color: "var(--fg-muted)" },
  running: { icon: "◐", color: "var(--accent)" },
  done: { icon: "●", color: "var(--green)" },
  // Amber, not red — a dead branch is normal (masterplan §4.2: "partial
  // failure is the normal case"), and a red X trains a visitor to read a
  // successful run as broken (phase-13-frontend.md).
  failed: { icon: "●", color: "var(--amber)" },
};

export interface PlanChecklistProps {
  items: ChecklistItem[];
}

export function PlanChecklist({ items }: PlanChecklistProps) {
  return (
    <ul
      data-testid="plan-checklist"
      style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}
    >
      {items.map((item) => {
        const style = STATUS_STYLE[item.status];
        return (
          <li
            key={item.nodeId}
            data-testid={`checklist-item-${item.status}`}
            data-node-id={item.nodeId}
            style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}
          >
            <span
              aria-hidden
              style={{
                color: style.color,
                display: "inline-block",
                width: 14,
                animation: item.status === "running" ? "pulse 1.2s ease-in-out infinite" : undefined,
              }}
            >
              {style.icon}
            </span>
            <span>{KIND_LABELS[item.kind] ?? item.kind}</span>
            <span className="sr-only" style={{ position: "absolute", left: -9999 }}>
              {item.status}
            </span>
            {item.status === "failed" && (
              <span style={{ color: "var(--amber)", fontSize: 12 }}>skipped, run continues</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
