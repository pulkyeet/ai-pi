import type {
  ApiErrorEnvelope,
  BenchmarkReportSummary,
  ClaimDrilldown,
  DisambiguationOverrides,
  FindingDrilldown,
  HealthResponse,
  Report,
  RunAccepted,
  RunStatus,
} from "./types";

export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
    public readonly correlationId: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { accessToken?: string } = {},
): Promise<T> {
  const { accessToken, headers, ...rest } = init;
  const finalHeaders = new Headers(headers);
  if (accessToken) {
    finalHeaders.set("Authorization", `Bearer ${accessToken}`);
  }
  if (rest.body && !finalHeaders.has("Content-Type")) {
    finalHeaders.set("Content-Type", "application/json");
  }

  const response = await fetch(`${apiBaseUrl()}${path}`, { ...rest, headers: finalHeaders });
  if (!response.ok) {
    let envelope: ApiErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // Non-JSON error body (e.g. an upstream proxy 502) — fall through to
      // the generic error below rather than crashing on `.error.code`.
    }
    throw new ApiError(
      envelope?.error.message ?? response.statusText,
      envelope?.error.code ?? "unknown_error",
      response.status,
      envelope?.error.correlation_id ?? "",
    );
  }
  return (await response.json()) as T;
}

export function getBenchmarkReports(): Promise<BenchmarkReportSummary[]> {
  return request<BenchmarkReportSummary[]>("/reports/benchmark", { cache: "force-cache" });
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { cache: "no-store" });
}

export function createRun(
  query: string,
  accessToken: string,
  turnstileToken?: string,
): Promise<RunAccepted> {
  return request<RunAccepted>("/runs", {
    method: "POST",
    accessToken,
    body: JSON.stringify({ query, turnstile_token: turnstileToken ?? null }),
  });
}

export function getRun(runId: string, accessToken?: string): Promise<RunStatus> {
  return request<RunStatus>(`/runs/${encodeURIComponent(runId)}`, {
    accessToken,
    cache: "no-store",
  });
}

export function getReport(runId: string, accessToken?: string): Promise<Report> {
  return request<Report>(`/runs/${encodeURIComponent(runId)}/report.json`, {
    accessToken,
    cache: "no-store",
  });
}

export function getClaimDrilldown(
  runId: string,
  claimId: number,
  accessToken?: string,
): Promise<ClaimDrilldown> {
  return request<ClaimDrilldown>(
    `/runs/${encodeURIComponent(runId)}/claims/${claimId}`,
    { accessToken, cache: "no-store" },
  );
}

export function getFindingDrilldown(
  runId: string,
  findingId: number,
  accessToken?: string,
): Promise<FindingDrilldown> {
  return request<FindingDrilldown>(
    `/runs/${encodeURIComponent(runId)}/findings/${findingId}`,
    { accessToken, cache: "no-store" },
  );
}

export function resolveDisambiguation(
  runId: string,
  overrides: DisambiguationOverrides,
  accessToken: string,
): Promise<RunAccepted> {
  return request<RunAccepted>(`/runs/${encodeURIComponent(runId)}`, {
    method: "PATCH",
    accessToken,
    body: JSON.stringify(overrides),
  });
}

export function eventsUrl(runId: string): string {
  return `${apiBaseUrl()}/runs/${encodeURIComponent(runId)}/events`;
}
