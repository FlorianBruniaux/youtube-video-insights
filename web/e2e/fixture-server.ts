import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";

export const SESSION_ID = "session1234567890abcdef1234567890ab";
export const PREVIEW_IDS = ["abc123DEF45", "xyz987QWE12"] as const;

type ResearchMode = "sufficiency" | "approval" | "acquiring" | "cancelled";

export interface RecordedMutation {
  readonly path: string;
  readonly body: unknown;
  readonly token: string | null;
}

export interface RecordedRead {
  readonly path: string;
  readonly query: Readonly<Record<string, readonly string[]>>;
}

export interface FixtureServer {
  readonly origin: string;
  readonly mutations: RecordedMutation[];
  readonly reads: RecordedRead[];
  resetResearch(mode?: ResearchMode): void;
  close(): Promise<void>;
}

const STATIC_ROOT = resolve(import.meta.dirname, "../../src/yt_insights/web/static");
const TOKEN = "fixture-mutation-token-0123456789abcdef";
const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const HASH_C = "c".repeat(64);

export async function startFixtureServer(): Promise<FixtureServer> {
  const mutations: RecordedMutation[] = [];
  const reads: RecordedRead[] = [];
  let researchMode: ResearchMode = "sufficiency";

  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      if (url.pathname.startsWith("/api/v1/")) {
        if (request.method === "GET") {
          reads.push({ path: url.pathname, query: queryRecord(url.searchParams) });
        }
        await serveApi(request, response, url, mutations, () => researchMode, (mode) => {
          researchMode = mode;
        });
        return;
      }
      await serveStatic(response, url.pathname);
    } catch {
      json(response, 500, { schema_version: 1, error: { code: "internal_error" } });
    }
  });

  await new Promise<void>((resolveListening, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolveListening());
  });
  const address = server.address();
  if (address === null || typeof address === "string") {
    server.close();
    throw new Error("Fixture server did not bind a loopback TCP port");
  }
  return {
    origin: `http://127.0.0.1:${address.port}`,
    mutations,
    reads,
    resetResearch(mode = "sufficiency") {
      researchMode = mode;
      mutations.splice(0);
      reads.splice(0);
    },
    close: () => new Promise<void>((resolveClose, reject) => {
      server.close((error) => error ? reject(error) : resolveClose());
    }),
  };
}

async function serveApi(
  request: IncomingMessage,
  response: ServerResponse,
  url: URL,
  mutations: RecordedMutation[],
  mode: () => ResearchMode,
  setMode: (mode: ResearchMode) => void,
): Promise<void> {
  const path = url.pathname;
  if (request.method === "GET") {
    if (path === "/api/v1/bootstrap") {
      json(response, 200, { schema_version: 1, mutation_token: TOKEN });
      return;
    }
    if (path === "/api/v1/status") {
      json(response, 200, {
        schema_version: 1,
        status: "ok",
        corpus: {
          health: "ready",
          videos: 12,
          transcripts: 11,
          documents_indexed: 11,
          passages_indexed: 42,
        },
      });
      return;
    }
    if (path === "/api/v1/search") {
      if (!isExactSearch(url.searchParams)) {
        json(response, 400, {
          schema_version: 1,
          error: { code: "invalid_request" },
        });
        return;
      }
      json(response, 200, searchResponse());
      return;
    }
    if (path === "/api/v1/sources") {
      json(response, 200, sourcesResponse());
      return;
    }
    if (path === "/api/v1/research/sessions") {
      json(response, 200, researchList(mode()));
      return;
    }
    if (path === `/api/v1/research/sessions/${SESSION_ID}`) {
      json(response, 200, researchResponse(mode(), true));
      return;
    }
    if (path === "/api/v1/exports") {
      json(response, 200, exportsResponse());
      return;
    }
    if (path === "/api/v1/jobs/source-preview-job") {
      json(response, 200, succeededJob("source-preview-job", "source_preview", sourcePreview()));
      return;
    }
    if (path === "/api/v1/jobs/source-acquisition-job") {
      json(response, 200, succeededJob("source-acquisition-job", "source_acquisition", acquisitionResult()));
      return;
    }
    if (path === "/api/v1/jobs/research-discovery-job") {
      setMode("approval");
      json(response, 200, succeededJob("research-discovery-job", "research_discovery", researchResponse("approval", false)));
      return;
    }
    json(response, 404, { schema_version: 1, error: { code: "not_found" } });
    return;
  }

  if (request.method !== "POST") {
    json(response, 405, { schema_version: 1, error: { code: "method_not_allowed" } });
    return;
  }
  if (request.headers["x-yt-insights-token"] !== TOKEN) {
    json(response, 403, { schema_version: 1, error: { code: "forbidden" } });
    return;
  }
  const body = await readJsonBody(request);
  mutations.push({
    path,
    body,
    token: typeof request.headers["x-yt-insights-token"] === "string"
      ? request.headers["x-yt-insights-token"]
      : null,
  });
  if (path === "/api/v1/sources/preview") {
    json(response, 202, { schema_version: 1, job_id: "source-preview-job" });
    return;
  }
  if (path === "/api/v1/sources/acquire") {
    json(response, 202, { schema_version: 1, job_id: "source-acquisition-job" });
    return;
  }
  if (path === `/api/v1/research/sessions/${SESSION_ID}/decisions`) {
    const decision = record(body).decision;
    setMode(decision === "refresh" ? "acquiring" : "cancelled");
    const nextMode = decision === "refresh" ? "acquiring" : "cancelled";
    const projected = researchResponse(nextMode, false);
    if (nextMode === "acquiring") {
      record(projected.session).state = "discovering";
    }
    json(response, 200, projected);
    return;
  }
  if (path === `/api/v1/research/sessions/${SESSION_ID}/discovery`) {
    json(response, 202, { schema_version: 1, job_id: "research-discovery-job" });
    return;
  }
  if (path === `/api/v1/research/sessions/${SESSION_ID}/approvals`) {
    setMode("acquiring");
    json(response, 200, researchResponse("acquiring", false));
    return;
  }
  if (path === `/api/v1/research/sessions/${SESSION_ID}/cancellations`) {
    setMode("cancelled");
    json(response, 200, researchResponse("cancelled", false));
    return;
  }
  json(response, 404, { schema_version: 1, error: { code: "not_found" } });
}

function isExactSearch(parameters: URLSearchParams): boolean {
  return parameters.toString() ===
    "q=local+inference&channel=local-ai&language=en&limit=20";
}

function queryRecord(
  parameters: URLSearchParams,
): Readonly<Record<string, readonly string[]>> {
  const result: Record<string, string[]> = {};
  for (const key of [...new Set(parameters.keys())].sort()) {
    result[key] = parameters.getAll(key);
  }
  return result;
}

async function serveStatic(response: ServerResponse, requestedPath: string): Promise<void> {
  let relativePath: string;
  if (/^\/research\/[A-Za-z0-9_-]{1,128}\/?$/.test(requestedPath)) {
    relativePath = "research/workspace/index.html";
  } else if (requestedPath === "/") {
    relativePath = "index.html";
  } else {
    const clean = requestedPath.replace(/^\/+/, "");
    relativePath = clean.endsWith("/") ? `${clean}index.html` : clean;
  }
  const candidate = resolve(STATIC_ROOT, relativePath);
  if (candidate !== STATIC_ROOT && !candidate.startsWith(`${STATIC_ROOT}${sep}`)) {
    text(response, 404, "Not found");
    return;
  }
  try {
    const metadata = await stat(candidate);
    if (!metadata.isFile()) throw new Error("not a file");
    const content = await readFile(candidate);
    response.writeHead(200, {
      "Content-Type": contentType(candidate),
      "Content-Length": String(content.byteLength),
      "Cache-Control": "no-store",
    });
    response.end(content);
  } catch {
    text(response, 404, "Not found");
  }
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.byteLength;
    if (size > 64 * 1024) throw new Error("fixture body too large");
    chunks.push(buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function json(response: ServerResponse, status: number, body: unknown): void {
  const encoded = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": String(encoded.byteLength),
    "Cache-Control": "no-store",
  });
  response.end(encoded);
}

function text(response: ServerResponse, status: number, body: string): void {
  response.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(body);
}

function contentType(path: string): string {
  return {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
  }[extname(path)] ?? "application/octet-stream";
}

function searchResponse(): unknown {
  return {
    schema_version: 1,
    hits: [{
      passage_id: HASH_A,
      rank: 1,
      score: -2.5,
      channel_id: "local-ai",
      channel: "Local AI Lab",
      title: "Run models on your laptop",
      language: "en",
      excerpt: "Local inference keeps research costs predictable.",
      start_seconds: 83,
      end_seconds: 106,
      url: "https://www.youtube.com/watch?v=abc123DEF45&t=83s",
    }],
    returned: 1,
    truncated: false,
  };
}

function sourcesResponse(): unknown {
  return {
    schema_version: 1,
    items: [{
      video_id: PREVIEW_IDS[0],
      title: "Local inference explained",
      published_at: "2026-08-20",
      languages: ["en"],
      sources: ["channel"],
      url: `https://www.youtube.com/watch?v=${PREVIEW_IDS[0]}`,
      artifact_count: 2,
      transcript_state: "available",
      index_state: "indexed",
    }],
    limit: 20,
    offset: 0,
  };
}

function sourcePreview(): unknown {
  return {
    fingerprint: HASH_A,
    source_kind: "channel",
    selected_count: 2,
    video_ids: [...PREVIEW_IDS],
    videos: PREVIEW_IDS.map((videoId, index) => ({
      video_id: videoId,
      title: index === 0 ? "Local inference explained" : "Production MLX",
      published_at: `2026-08-${20 + index}`,
      url: `https://www.youtube.com/watch?v=${videoId}`,
    })),
    videos_returned: 2,
    videos_truncated: false,
    language: "en",
    analyze: false,
    requires_confirmation: true,
    excluded_count: 0,
    discovery_error_count: 0,
  };
}

function acquisitionResult(): unknown {
  return {
    selected: 2,
    transcripts_ready: 2,
    insights_ready: 0,
    failure_count: 0,
    exclusion_count: 0,
    items: PREVIEW_IDS.map((videoId) => ({
      video_id: videoId,
      status: "acquired",
      error_code: null,
      source_sha256: HASH_B,
    })),
    exit_code: 0,
  };
}

function researchState(mode: ResearchMode): string {
  return {
    sufficiency: "awaiting_sufficiency_confirmation",
    approval: "awaiting_candidate_approval",
    acquiring: "acquiring",
    cancelled: "cancelled",
  }[mode];
}

function researchAction(mode: ResearchMode): string | null {
  if (mode === "sufficiency") return "confirm_sufficiency_or_refresh";
  if (mode === "approval") return "approve_candidates_or_cancel";
  return null;
}

function researchResponse(
  mode: ResearchMode,
  history: boolean,
): Record<string, unknown> {
  const response: Record<string, unknown> = {
    schema_version: 1,
    session: {
      session_id: SESSION_ID,
      topic: "Local inference",
      queries: ["local inference"],
      languages: ["en"],
      freshness_profile: "standard",
      discovery_fingerprint: HASH_A,
      state: researchState(mode),
      revision: mode === "sufficiency" ? 1 : 2,
      retry_target: null,
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-31T10:00:00Z",
    },
    assessment: {
      created_at: "2026-08-31T10:00:00Z",
      snapshot: { search_generation: HASH_B, catalog_generation: HASH_C },
      coverage: {
        matched_passages: 1,
        matched_videos: 1,
        distinct_channels: 1,
        queries_with_zero_hits: [],
        newest_source_published_at: "2026-08-20",
        unknown_publication_date_count: 0,
      },
      freshness: {
        profile: "standard",
        maximum_age_days: 30,
        last_successful_discovery_at: null,
        stale: true,
        reason: "Newer evidence may exist.",
      },
      passages: [{
        query: "local inference",
        passage_id: HASH_A,
        video_id: PREVIEW_IDS[0],
        channel_id: "local-ai",
        rank: 1,
        url: `https://www.youtube.com/watch?v=${PREVIEW_IDS[0]}`,
        excerpt: "Local evidence first.",
        source_sha256: HASH_B,
      }],
      videos: [{
        query: "local inference",
        video_id: PREVIEW_IDS[0],
        source_keys: ["channel:local-ai"],
        title: "Run MLX locally",
        published_at: "2026-08-20",
        rank: 1,
        watch_url: `https://www.youtube.com/watch?v=${PREVIEW_IDS[0]}`,
      }],
    },
    candidates: mode === "approval" ? [{
      video_id: PREVIEW_IDS[1],
      title: "Fresh local inference benchmark",
      channel_id: "bench-lab",
      channel_title: "Benchmark Lab",
      published_at: "2026-08-30",
      watch_url: `https://www.youtube.com/watch?v=${PREVIEW_IDS[1]}`,
      matched_queries: ["local inference"],
      original_rank: 1,
      status: "candidate",
    }] : null,
    required_user_action: researchAction(mode),
    error_code: null,
    acquisition_history: [],
    acquisition_history_truncated: false,
  };
  if (history) {
    response.history = {
      decisions: [],
      events: [{
        event_id: 1,
        from_state: "assessing",
        to_state: researchState(mode),
        event_code: "assessment_completed",
        created_at: "2026-08-31T10:00:00Z",
      }],
      decisions_truncated: false,
      events_truncated: false,
    };
  }
  return response;
}

function researchList(mode: ResearchMode): unknown {
  const response = researchResponse(mode, false);
  return {
    schema_version: 1,
    items: [{
      ...record(response.session),
      required_user_action: response.required_user_action,
    }],
    limit: 5,
    offset: 0,
  };
}

function exportsResponse(): unknown {
  return {
    schema_version: 1,
    items: [],
    limit: 20,
    truncated: false,
    inventory_complete: true,
    inventory_examined: 0,
    inventory_limit: 32,
  };
}

function succeededJob(jobId: string, kind: string, result: unknown): unknown {
  return {
    schema_version: 1,
    job: { job_id: jobId, kind, status: "succeeded", result, error_code: null },
  };
}
