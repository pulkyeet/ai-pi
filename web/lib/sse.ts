import type { RunEvent } from "./types";

// A fetch-based SSE client, not the browser's native `EventSource` —
// deliberately, because `GET /runs/{id}/events` needs an `Authorization:
// Bearer` header for a non-public run (src/api/web/auth.py's
// `optional_user`), and `EventSource` cannot send custom headers. Building
// on `fetch` also means reconnect-with-`Last-Event-ID` is explicit code
// here instead of an opaque browser behaviour, which is what makes it
// possible to unit-test (phase-13-frontend.md: "SSE client reconnect:
// resumes from Last-Event-ID, no duplicate rendering").

export interface ParsedSSEEvent {
  id: string | null;
  event: string | null;
  data: string;
}

// Parses one "id: ...\nevent: ...\ndata: ...\n\n"-shaped block. Per the SSE
// spec a block may carry multiple `data:` lines (joined with `\n`); this
// server never emits more than one, but the parser stays correct either way.
export function parseSSEBlock(block: string): ParsedSSEEvent | null {
  const lines = block.split("\n").filter((line) => line.length > 0);
  if (lines.length === 0) return null;

  let id: string | null = null;
  let event: string | null = null;
  const dataLines: string[] = [];

  for (const line of lines) {
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { id, event, data: dataLines.join("\n") };
}

// Splits a raw SSE byte stream into `{ events, remainder }`: complete
// blocks (terminated by a blank line) plus whatever incomplete tail should
// be prepended to the next chunk.
export function splitSSEBuffer(buffer: string): { events: ParsedSSEEvent[]; remainder: string } {
  const parts = buffer.split("\n\n");
  const remainder = parts.pop() ?? "";
  const events = parts.map(parseSSEBlock).filter((e): e is ParsedSSEEvent => e !== null);
  return { events, remainder };
}

export interface StreamRunEventsOptions {
  accessToken?: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  retryDelayMs?: number;
  sinceId?: number;
}

export interface RunEventEnvelope {
  id: number;
  event: RunEvent;
}

const TERMINAL_EVENT_TYPES = new Set(["report.ready"]);

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

// Streams events from `url`, reconnecting with `Last-Event-ID` on any drop
// (network blip, server restart) until a terminal event arrives or `signal`
// aborts. Never re-yields an event id already seen — the reconnect resumes
// strictly after `lastId`, and the server's own replay (`api.web.sse.
// stream_events`) is itself id-ordered and gap-free, so no client-side
// dedup beyond "resume after the last id seen" is needed.
export async function* streamRunEvents(
  url: string,
  options: StreamRunEventsOptions = {},
): AsyncGenerator<RunEventEnvelope> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const retryDelayMs = options.retryDelayMs ?? 1000;
  let lastId = options.sinceId ?? 0;

  while (!options.signal?.aborted) {
    let response: Response;
    try {
      const headers = new Headers({ Accept: "text/event-stream" });
      if (options.accessToken) headers.set("Authorization", `Bearer ${options.accessToken}`);
      if (lastId > 0) headers.set("Last-Event-ID", String(lastId));
      response = await fetchImpl(url, { headers, signal: options.signal });
    } catch {
      if (options.signal?.aborted) return;
      await sleep(retryDelayMs, options.signal);
      continue;
    }

    if (!response.ok || !response.body) {
      await sleep(retryDelayMs, options.signal);
      continue;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawTerminal = false;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remainder } = splitSSEBuffer(buffer);
        buffer = remainder;
        for (const raw of events) {
          const id = raw.id !== null ? Number(raw.id) : lastId + 1;
          // The server's cursor (`api.web.sse.stream_events`) is strictly
          // increasing and a reconnect always resumes strictly after
          // `Last-Event-ID`, so an id at or below what we've already
          // yielded is a duplicate delivery, not new data — skip it rather
          // than re-render the same finding/task twice.
          if (id <= lastId) continue;
          lastId = id;
          const parsed = JSON.parse(raw.data) as RunEvent;
          yield { id, event: parsed };
          if (raw.event && TERMINAL_EVENT_TYPES.has(raw.event)) sawTerminal = true;
        }
        if (sawTerminal) return;
      }
    } finally {
      reader.releaseLock();
    }

    if (sawTerminal || options.signal?.aborted) return;
    await sleep(retryDelayMs, options.signal);
  }
}
