import type { Plan, RunEvent } from "./types";

export type ChecklistStatus = "pending" | "running" | "done" | "failed";

export interface ChecklistItem {
  nodeId: string;
  kind: string;
  status: ChecklistStatus;
}

// The public SSE contract (`api.models.events.TaskStartedEvent` etc.) never
// carries `node_key` — only the executor's *internal* telemetry does
// (`api.executor.protocol`'s own `TaskStarted`/... — see that module's
// `node_key: str` field vs. the web event's bare `task_id: int`). So a
// streamed `task.*` event can only be matched back to a `PlanNode` by
// `kind`, best-effort: the first still-pending node of that kind is
// assigned to a `task_id` the moment it starts, and every later event for
// that same `task_id` updates that same checklist row. Nodes of the same
// kind are fungible leased work (`SKIP LOCKED`), so which physical node a
// given task_id "is" doesn't matter for a checklist — only that each row
// ticks exactly once.
export function initialChecklist(plan: Plan): ChecklistItem[] {
  return plan.nodes.map((node) => ({ nodeId: node.id, kind: node.kind, status: "pending" }));
}

export function reduceChecklist(
  items: ChecklistItem[],
  taskIdToNodeId: Map<number, string>,
  event: RunEvent,
): ChecklistItem[] {
  if (event.type === "task.started") {
    if (taskIdToNodeId.has(event.task_id)) return items; // duplicate delivery
    const next = items.find((item) => item.kind === event.kind && item.status === "pending");
    if (!next) return items;
    taskIdToNodeId.set(event.task_id, next.nodeId);
    return items.map((item) =>
      item.nodeId === next.nodeId ? { ...item, status: "running" } : item,
    );
  }
  if (event.type === "task.completed" || event.type === "task.failed") {
    const nodeId = taskIdToNodeId.get(event.task_id);
    if (!nodeId) return items;
    const status: ChecklistStatus = event.type === "task.completed" ? "done" : "failed";
    return items.map((item) => (item.nodeId === nodeId ? { ...item, status } : item));
  }
  return items;
}
