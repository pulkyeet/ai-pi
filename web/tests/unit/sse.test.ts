import { describe, expect, it, vi } from "vitest";
import { parseSSEBlock, splitSSEBuffer, streamRunEvents } from "@/lib/sse";

function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]));
        i += 1;
      } else {
        controller.close();
      }
    },
  });
}

describe("parseSSEBlock / splitSSEBuffer", () => {
  it("parses a single well-formed block", () => {
    const parsed = parseSSEBlock('id: 3\nevent: task.started\ndata: {"a":1}');
    expect(parsed).toEqual({ id: "3", event: "task.started", data: '{"a":1}' });
  });

  it("splits a buffer into complete blocks plus a remainder", () => {
    const { events, remainder } = splitSSEBuffer(
      'id: 1\nevent: plan.created\ndata: {}\n\nid: 2\nevent: task.st',
    );
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ id: "1", event: "plan.created", data: "{}" });
    expect(remainder).toBe("id: 2\nevent: task.st");
  });
});

describe("streamRunEvents", () => {
  it("reconnects with Last-Event-ID after a mid-stream drop, with no duplicate events", async () => {
    const firstBody = sseBody([
      'id: 1\nevent: plan.created\ndata: {"type":"plan.created","run_id":"r1","plan":{"nodes":[],"edges":[],"total_budget_weight":0}}\n\n',
      'id: 2\nevent: task.started\ndata: {"type":"task.started","run_id":"r1","task_id":9,"kind":"profile_product"}\n\n',
    ]);
    const secondBody = sseBody([
      // The server may legitimately re-send the boundary event on replay;
      // the client must not re-yield it.
      'id: 2\nevent: task.started\ndata: {"type":"task.started","run_id":"r1","task_id":9,"kind":"profile_product"}\n\n',
      'id: 3\nevent: report.ready\ndata: {"type":"report.ready","run_id":"r1"}\n\n',
    ]);

    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response(firstBody, { status: 200 }))
      .mockResolvedValueOnce(new Response(secondBody, { status: 200 }));

    const received: { id: number; type: string }[] = [];
    for await (const { id, event } of streamRunEvents("https://api.test/runs/r1/events", {
      fetchImpl,
      retryDelayMs: 0,
    })) {
      received.push({ id, type: event.type });
    }

    expect(received).toEqual([
      { id: 1, type: "plan.created" },
      { id: 2, type: "task.started" },
      { id: 3, type: "report.ready" },
    ]);
    expect(fetchImpl).toHaveBeenCalledTimes(2);

    const secondCallHeaders = fetchImpl.mock.calls[1]?.[1]?.headers as Headers;
    expect(secondCallHeaders.get("Last-Event-ID")).toBe("2");
  });

  it("sends the Authorization header derived from accessToken", async () => {
    const body = sseBody(['id: 1\nevent: report.ready\ndata: {"type":"report.ready","run_id":"r1"}\n\n']);
    const fetchImpl = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));

    const events = [];
    for await (const evt of streamRunEvents("https://api.test/runs/r1/events", {
      fetchImpl,
      accessToken: "tok-123",
      retryDelayMs: 0,
    })) {
      events.push(evt);
    }

    const headers = fetchImpl.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer tok-123");
    expect(events).toHaveLength(1);
  });

  it("stops without reconnecting once an AbortSignal fires", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn().mockImplementation(() => {
      controller.abort();
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });

    const events = [];
    for await (const evt of streamRunEvents("https://api.test/runs/r1/events", {
      fetchImpl,
      signal: controller.signal,
      retryDelayMs: 0,
    })) {
      events.push(evt);
    }

    expect(events).toHaveLength(0);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
