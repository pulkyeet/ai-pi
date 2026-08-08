import { describe, expect, it } from "vitest";
import { initialChecklist, reduceChecklist } from "@/lib/checklist";
import type { Plan, RunEvent } from "@/lib/types";

const plan: Plan = {
  nodes: [
    { id: "t1", kind: "discover_competitors", args: {}, budget_weight: 3 },
    { id: "t2", kind: "profile_product", args: {}, budget_weight: 2 },
    { id: "t3", kind: "profile_product", args: {}, budget_weight: 2 },
  ],
  edges: [],
  total_budget_weight: 7,
};

function apply(events: RunEvent[]) {
  let items = initialChecklist(plan);
  const taskIdToNodeId = new Map<number, string>();
  for (const event of events) {
    items = reduceChecklist(items, taskIdToNodeId, event);
  }
  return items;
}

describe("checklist reducer", () => {
  it("starts every node pending", () => {
    expect(initialChecklist(plan).every((item) => item.status === "pending")).toBe(true);
  });

  it("ticks a node to running on task.started and done on task.completed", () => {
    const items = apply([
      { type: "task.started", run_id: "r1", task_id: 101, kind: "discover_competitors" },
      { type: "task.completed", run_id: "r1", task_id: 101, kind: "discover_competitors", cost_usd: 0.01, latency_ms: 200 },
    ]);
    expect(items.find((i) => i.nodeId === "t1")?.status).toBe("done");
  });

  it("marks a failed task amber (failed), not as an error state distinct from the model", () => {
    const items = apply([
      { type: "task.started", run_id: "r1", task_id: 101, kind: "discover_competitors" },
      { type: "task.failed", run_id: "r1", task_id: 101, kind: "discover_competitors", error: "timeout" },
    ]);
    expect(items.find((i) => i.nodeId === "t1")?.status).toBe("failed");
  });

  it("assigns same-kind task ids to distinct nodes in arrival order, not by declared node id", () => {
    const items = apply([
      // Two profile_product nodes (t2, t3) are fungible leased work — the
      // executor may complete task_id 55 before task_id 54 even starts.
      { type: "task.started", run_id: "r1", task_id: 55, kind: "profile_product" },
      { type: "task.completed", run_id: "r1", task_id: 55, kind: "profile_product", cost_usd: null, latency_ms: null },
      { type: "task.started", run_id: "r1", task_id: 54, kind: "profile_product" },
    ]);
    const statuses = items.filter((i) => i.kind === "profile_product").map((i) => i.status);
    expect(statuses.sort()).toEqual(["done", "running"]);
  });

  it("ignores a duplicate task.started delivery for an already-assigned task_id", () => {
    const items = apply([
      { type: "task.started", run_id: "r1", task_id: 101, kind: "discover_competitors" },
      { type: "task.started", run_id: "r1", task_id: 101, kind: "discover_competitors" },
    ]);
    // A second start for the same task_id must not steal t2/t3's slot.
    expect(items.filter((i) => i.status === "running")).toHaveLength(1);
  });
});
