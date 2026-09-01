import type {
  AcquisitionErrorCode,
  AcquisitionHistoryAttempt,
  AcquisitionItemStatus,
  ApiGetResponse,
  ApiPath,
  ApiPostResponse,
  BootstrapResponse,
  CandidateStatus,
  ExportsResponse,
  ExportCreatedResponse,
  ExportItem,
  FreshnessProfile,
  Job,
  JobAcceptedResponse,
  JobKind,
  JobResponse,
  JobResultError,
  PublicApiErrorCode,
  ResearchAssessment,
  ResearchCandidate,
  ResearchListResponse,
  ResearchErrorCode,
  ResearchResponse,
  ResearchSessionCore,
  ResearchSessionSummary,
  ResearchState,
  ResearchTimeline,
  SearchHit,
  SearchResponse,
  SourceAcquisitionResult,
  SourceKind,
  SourceItem,
  SourcePreviewResult,
  SourcesResponse,
  StatusResponse,
} from "./types";

const MUTATION_TOKEN_HEADER = "X-YT-Insights-Token";
const MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_HITS = 20;
const MAX_RESEARCH_QUERIES = 8;
const MAX_RESEARCH_EVIDENCE = 160;
const MAX_RESEARCH_CANDIDATES = 10;
const MAX_TIMELINE_ITEMS = 100;
const MAX_ACQUISITION_ATTEMPTS = 100;
const MAX_ACQUISITION_ITEMS = 1_000;
const MAX_SOURCE_METADATA_VALUES = 20;
const MAX_SELECTED_VIDEOS = 1_000;
const MAX_PLAN_IDENTITY_BYTES = 524_288;
const MAX_ACQUISITION_DIAGNOSTICS =
  MAX_PLAN_IDENTITY_BYTES + MAX_SELECTED_VIDEOS + 2;
const MAX_PUBLIC_STRING_CODEPOINTS = 2_048;
const PUBLIC_ERROR_CODES = new Set<string>([
  "invalid_request",
  "forbidden",
  "method_not_allowed",
  "not_found",
  "plan_changed",
  "stale_revision",
  "workflow_conflict",
  "idempotency_conflict",
  "request_in_progress",
  "job_queue_full",
  "jobs_unavailable",
  "search_unavailable",
  "catalog_unavailable",
  "research_unavailable",
  "exports_unavailable",
  "internal_error",
  "server_busy",
  "server_shutting_down",
]);
const ERROR_STATUSES: Readonly<
  Record<Exclude<PublicApiErrorCode, "unexpected_response">, readonly number[]>
> = {
  invalid_request: [400, 413, 414, 431, 505],
  forbidden: [403],
  method_not_allowed: [405],
  not_found: [404],
  plan_changed: [409],
  stale_revision: [409],
  workflow_conflict: [409],
  idempotency_conflict: [409],
  request_in_progress: [409],
  job_queue_full: [429],
  jobs_unavailable: [503],
  search_unavailable: [503],
  catalog_unavailable: [503],
  research_unavailable: [503],
  exports_unavailable: [503],
  internal_error: [500],
  server_busy: [503],
  server_shutting_down: [503],
};
const RESEARCH_STATES = new Set<ResearchState>([
  "assessing",
  "awaiting_sufficiency_confirmation",
  "discovering",
  "awaiting_candidate_approval",
  "acquiring",
  "reindexing",
  "completed",
  "failed_retryable",
  "cancelled",
]);
const FRESHNESS_PROFILES = new Set<FreshnessProfile>([
  "fast",
  "standard",
  "stable",
  "historical",
]);
const CANDIDATE_STATUSES = new Set<CandidateStatus>([
  "candidate",
  "approved",
  "acquired",
  "already_present",
  "no_transcript",
  "failed_retryable",
]);
const JOB_KINDS = new Set<JobKind>([
  "source_preview",
  "source_acquisition",
  "research_discovery",
  "research_acquisition",
  "research_retry",
]);
const SOURCE_KINDS = new Set<SourceKind>([
  "video",
  "playlist",
  "channel",
  "batch",
]);
const ACQUISITION_ITEM_STATUSES = new Set<AcquisitionItemStatus>([
  "acquired",
  "already_present",
  "no_transcript",
  "failed_retryable",
]);
const ACQUISITION_ERROR_CODES = new Set<AcquisitionErrorCode>([
  "acquisition_unavailable",
  "cache_read_failed",
  "download_failed",
  "no_transcript",
  "acquisition_failed",
]);
const RESEARCH_ERROR_CODES = new Set<ResearchErrorCode>([
  "acquisition_in_progress",
  "acquisition_unavailable",
  "discovery_unavailable",
  "index_refresh_failed",
  "local_index_unavailable",
  "partial_acquisition_failed",
  "retry_in_progress",
  "research_unavailable",
]);
const ACQUISITION_ATTEMPT_STATUSES = new Set([
  "running",
  "failed_retryable",
  "completed",
] as const);
const JOB_RESULT_ERROR_CODES = new Set([
  "plan_too_large",
  "plan_changed",
  "stale_revision",
  "workflow_conflict",
  "not_found",
  "operation_failed",
] as const);
const REQUIRED_ACTIONS = new Set([
  "confirm_sufficiency_or_refresh",
  "approve_candidates_or_cancel",
] as const);
const SHA256 = /^[0-9a-f]{64}$/;
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const JOB_ID = /^[A-Za-z0-9_-]{1,200}$/;
const EXPORT_OPEN_URL = /^\/api\/v1\/exports\/([0-9a-f]{64})\/dossier$/;

let mutationToken: string | null = null;

export class PublicApiError extends Error {
  readonly code: PublicApiErrorCode;
  readonly status: number;

  constructor(code: PublicApiErrorCode, status: number) {
    super(publicErrorMessage(code));
    this.name = "PublicApiError";
    this.code = code;
    this.status = status;
  }

  toJSON(): { readonly code: PublicApiErrorCode; readonly status: number } {
    return { code: this.code, status: this.status };
  }
}

export async function apiGet<T extends ApiGetResponse = ApiGetResponse>(
  path: ApiPath,
  signal?: AbortSignal,
): Promise<T> {
  const route = requireApiPath(path, "GET");
  const response = await fetch(path, {
    credentials: "same-origin",
    method: "GET",
    ...(signal === undefined ? {} : { signal }),
  });
  const payload = await requireJson(response);
  if (!response.ok) {
    const error = publicResponseError(payload, response.status);
    if (error.status === 403 && error.code === "forbidden")
      mutationToken = null;
    throw error;
  }
  if (response.status !== 200) throw unexpected(response.status);
  const parsed = parseGetResponse(route, payload, response.status);
  return parsed as T;
}

export async function apiPost<T extends ApiPostResponse = ApiPostResponse>(
  path: ApiPath,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const route = requireApiPath(path, "POST");
  const token = await getMutationToken(signal);
  const response = await fetch(path, {
    body: stringifyBody(body),
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      [MUTATION_TOKEN_HEADER]: token,
    },
    method: "POST",
    ...(signal === undefined ? {} : { signal }),
  });
  const payload = await requireJson(response);
  if (!response.ok) {
    const error = publicResponseError(payload, response.status);
    if (error.status === 403 && error.code === "forbidden")
      mutationToken = null;
    throw error;
  }
  const parsed = parsePostResponse(route, payload, response.status);
  return parsed as T;
}

async function getMutationToken(signal?: AbortSignal): Promise<string> {
  if (mutationToken !== null) return mutationToken;
  const response = await fetch("/api/v1/bootstrap", {
    credentials: "same-origin",
    method: "GET",
    ...(signal === undefined ? {} : { signal }),
  });
  const payload = await requireJson(response);
  if (!response.ok) throw publicResponseError(payload, response.status);
  if (response.status !== 200) throw unexpected(response.status);
  const parsed = parseBootstrap(payload, response.status);
  mutationToken = parsed.mutation_token;
  return mutationToken;
}

function stringifyBody(body: unknown): string {
  try {
    const serialized = JSON.stringify(body);
    if (serialized === undefined)
      throw new TypeError("Body is not JSON serializable");
    return serialized;
  } catch (error: unknown) {
    if (error instanceof TypeError) throw error;
    throw new TypeError("Body is not JSON serializable");
  }
}

async function requireJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("Content-Type") ?? "";
  if (
    contentType.split(";", 1)[0]?.trim().toLowerCase() !== "application/json"
  ) {
    rejectHeaders(response);
  }
  const declaredLength = response.headers.get("Content-Length");
  let expectedBytes: number | null = null;
  if (declaredLength !== null) {
    if (!/^(0|[1-9][0-9]{0,6})$/.test(declaredLength)) {
      rejectHeaders(response);
    }
    expectedBytes = Number(declaredLength);
    if (
      !Number.isSafeInteger(expectedBytes) ||
      expectedBytes > MAX_JSON_RESPONSE_BYTES
    ) {
      rejectHeaders(response);
    }
  }
  if (response.body !== null) {
    return readBoundedJsonStream(response, expectedBytes);
  }
  try {
    return await response.json();
  } catch (error: unknown) {
    if (isAbortError(error)) throw error;
    throw unexpected(response.status);
  }
}

function rejectHeaders(response: Response): never {
  const error = unexpected(response.status);
  const body = response.body;
  if (body !== null) {
    try {
      void body.cancel().catch(() => undefined);
    } catch {
      // The fixed validation error remains authoritative.
    }
  }
  throw error;
}

async function readBoundedJsonStream(
  response: Response,
  expectedBytes: number | null,
): Promise<unknown> {
  const body = response.body;
  if (body === null) throw unexpected(response.status);
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      received += next.value.byteLength;
      if (received > MAX_JSON_RESPONSE_BYTES) {
        try {
          await reader.cancel();
        } catch {
          // The fixed client error below remains authoritative.
        }
        throw unexpected(response.status);
      }
      chunks.push(next.value);
    }
  } catch (error: unknown) {
    if (error instanceof PublicApiError || isAbortError(error)) throw error;
    throw unexpected(response.status);
  } finally {
    reader.releaseLock();
  }
  if (expectedBytes !== null && received !== expectedBytes) {
    throw unexpected(response.status);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return JSON.parse(text) as unknown;
  } catch (error: unknown) {
    if (isAbortError(error)) throw error;
    throw unexpected(response.status);
  }
}

function publicResponseError(payload: unknown, status: number): PublicApiError {
  const object = record(payload, status);
  requireExactKeys(object, ["schema_version", "error"], status);
  requireSchemaVersion(object.schema_version, status);
  const error = record(object.error, status);
  requireExactKeys(error, ["code"], status);
  const code = stringValue(error.code, status);
  if (!isPublicErrorCode(code) || !ERROR_STATUSES[code].includes(status))
    throw unexpected(status);
  return new PublicApiError(code, status);
}

function parseBootstrap(payload: unknown, status: number): BootstrapResponse {
  const object = record(payload, status);
  requireExactKeys(object, ["schema_version", "mutation_token"], status);
  requireSchemaVersion(object.schema_version, status);
  const token = stringValue(object.mutation_token, status);
  const tokenCodePoints = unicodeCodePointLength(token, 500);
  if (tokenCodePoints < 32 || tokenCodePoints > 500)
    throw unexpected(status);
  return { schema_version: 1, mutation_token: token };
}

function parseGetResponse(
  route: string,
  payload: unknown,
  status: number,
): ApiGetResponse {
  if (route === "/api/v1/status") return parseStatus(payload, status);
  if (route === "/api/v1/search") return parseSearch(payload, status);
  if (route === "/api/v1/sources") return parseSources(payload, status);
  if (route === "/api/v1/research/sessions")
    return parseResearchList(payload, status);
  if (/^\/api\/v1\/research\/sessions\/[A-Za-z0-9_-]{1,128}$/.test(route)) {
    return parseResearch(payload, status, true);
  }
  if (/^\/api\/v1\/jobs\/[A-Za-z0-9_-]{1,200}$/.test(route)) {
    return parseJobResponse(payload, status);
  }
  if (route === "/api/v1/exports") return parseExports(payload, status);
  throw unexpected(status);
}

function parsePostResponse(
  route: string,
  payload: unknown,
  status: number,
): ApiPostResponse {
  if (
    route === "/api/v1/sources/preview" ||
    route === "/api/v1/sources/acquire" ||
    /\/(discovery|acquisition|retry)$/.test(route)
  ) {
    if (status !== 202) throw unexpected(status);
    return parseJobAccepted(payload, status);
  }
  if (
    route === "/api/v1/research/sessions" ||
    /\/(decisions|approvals)$/.test(route)
  ) {
    if (status !== 200) throw unexpected(status);
    return parseResearch(payload, status, false);
  }
  if (/\/exports$/.test(route)) {
    if (status !== 200) throw unexpected(status);
    return parseExportCreated(payload, status);
  }
  throw unexpected(status);
}

function parseStatus(payload: unknown, status: number): StatusResponse {
  const object = versioned(payload, ["status", "corpus"], status);
  if (object.status !== "ok") throw unexpected(status);
  const corpus = record(object.corpus, status);
  requireExactKeys(
    corpus,
    [
      "health",
      "videos",
      "transcripts",
      "documents_indexed",
      "passages_indexed",
    ],
    status,
  );
  if (corpus.health !== "ready" && corpus.health !== "partial")
    throw unexpected(status);
  const documents = nullableCount(corpus.documents_indexed, status);
  const passages = nullableCount(corpus.passages_indexed, status);
  if (
    (corpus.health === "ready" && (documents === null || passages === null)) ||
    (corpus.health === "partial" && (documents !== null || passages !== null))
  ) {
    throw unexpected(status);
  }
  return {
    schema_version: 1,
    status: "ok",
    corpus: {
      health: corpus.health,
      videos: count(corpus.videos, status),
      transcripts: count(corpus.transcripts, status),
      documents_indexed: documents,
      passages_indexed: passages,
    },
  };
}

function parseSearch(payload: unknown, status: number): SearchResponse {
  const object = versioned(payload, ["hits", "returned", "truncated"], status);
  const hits = arrayValue(object.hits, status, MAX_SEARCH_HITS).map((item) =>
    parseSearchHit(item, status),
  );
  const returned = count(object.returned, status);
  if (returned !== hits.length || returned > MAX_SEARCH_HITS)
    throw unexpected(status);
  return {
    schema_version: 1,
    hits,
    returned,
    truncated: booleanValue(object.truncated, status),
  };
}

function parseSearchHit(value: unknown, status: number): SearchHit {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "passage_id",
      "rank",
      "score",
      "channel_id",
      "channel",
      "title",
      "language",
      "excerpt",
      "start_seconds",
      "end_seconds",
      "url",
    ],
    status,
  );
  const passageId = stringValue(object.passage_id, status, 64);
  if (!SHA256.test(passageId)) throw unexpected(status);
  const startSeconds = finiteNumber(object.start_seconds, status);
  const endSeconds = finiteNumber(object.end_seconds, status);
  if (startSeconds < 0 || endSeconds < startSeconds) throw unexpected(status);
  return {
    passage_id: passageId,
    rank: positiveSafeInteger(object.rank, status, MAX_SEARCH_HITS),
    score: finiteNumber(object.score, status),
    channel_id: stringValue(object.channel_id, status, 200),
    channel: stringValue(object.channel, status, 200),
    title: stringValue(object.title, status, 300),
    language: stringValue(object.language, status, 64),
    excerpt: stringValue(object.excerpt, status, 1_500),
    start_seconds: startSeconds,
    end_seconds: endSeconds,
    url: stringValue(object.url, status, 2_048),
  };
}

function parseSources(payload: unknown, status: number): SourcesResponse {
  const object = versioned(payload, ["items", "limit", "offset"], status);
  const limit = pageLimit(object.limit, status);
  return {
    schema_version: 1,
    items: arrayValue(object.items, status, limit).map((item) =>
      parseSource(item, status),
    ),
    limit,
    offset: count(object.offset, status),
  };
}

function parseSource(value: unknown, status: number): SourceItem {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "video_id",
      "title",
      "published_at",
      "languages",
      "sources",
      "url",
      "artifact_count",
      "transcript_state",
      "index_state",
    ],
    status,
  );
  const videoId = exactVideoId(object.video_id, status);
  const transcriptState = object.transcript_state;
  const indexState = object.index_state;
  if (transcriptState !== "available" && transcriptState !== "missing")
    throw unexpected(status);
  if (
    indexState !== "indexed" &&
    indexState !== "not_indexed" &&
    indexState !== "unknown"
  ) {
    throw unexpected(status);
  }
  return {
    video_id: videoId,
    title: stringValue(object.title, status, 1_000),
    published_at: nullableDate(object.published_at, status),
    languages: stringArray(
      object.languages,
      status,
      MAX_SOURCE_METADATA_VALUES,
      32,
    ),
    sources: stringArray(
      object.sources,
      status,
      MAX_SOURCE_METADATA_VALUES,
      200,
    ),
    url: canonicalWatchUrl(object.url, videoId, status),
    artifact_count: count(object.artifact_count, status),
    transcript_state: transcriptState,
    index_state: indexState,
  };
}

function parseResearchList(
  payload: unknown,
  status: number,
): ResearchListResponse {
  const object = versioned(payload, ["items", "limit", "offset"], status);
  const limit = pageLimit(object.limit, status);
  return {
    schema_version: 1,
    items: arrayValue(object.items, status, limit).map((item) =>
      parseSessionSummary(item, status),
    ),
    limit,
    offset: count(object.offset, status),
  };
}

function parseSessionSummary(
  value: unknown,
  status: number,
): ResearchSessionSummary {
  const object = record(value, status);
  const core = parseSessionObject(object, status, true);
  const action = requiredAction(object.required_user_action, status);
  requireActionMatchesState(core.state, action, status);
  return { ...core, required_user_action: action } as ResearchSessionSummary;
}

function parseSession(value: unknown, status: number): ResearchSessionCore {
  return parseSessionObject(record(value, status), status, false);
}

function parseSessionObject(
  object: Record<string, unknown>,
  status: number,
  withAction: boolean,
): ResearchSessionCore {
  const fields = [
    "session_id",
    "topic",
    "queries",
    "languages",
    "freshness_profile",
    "discovery_fingerprint",
    "state",
    "revision",
    "retry_target",
    "created_at",
    "updated_at",
  ];
  requireExactKeys(
    object,
    withAction ? [...fields, "required_user_action"] : fields,
    status,
  );
  const profile = enumValue(
    object.freshness_profile,
    FRESHNESS_PROFILES,
    status,
  );
  const state = enumValue(object.state, RESEARCH_STATES, status);
  const retryTarget =
    object.retry_target === null
      ? null
      : enumValue(object.retry_target, RESEARCH_STATES, status);
  const fingerprint = stringValue(object.discovery_fingerprint, status, 64);
  if (!SHA256.test(fingerprint)) throw unexpected(status);
  const sessionId = stringValue(object.session_id, status, 128);
  if (!SESSION_ID.test(sessionId)) throw unexpected(status);
  const queries = stringArray(
    object.queries,
    status,
    MAX_RESEARCH_QUERIES,
    500,
  );
  if (queries.length === 0) throw unexpected(status);
  return {
    session_id: sessionId,
    topic: nonEmptyString(object.topic, status, 500),
    queries,
    languages: stringArray(
      object.languages,
      status,
      MAX_SOURCE_METADATA_VALUES,
      500,
    ),
    freshness_profile: profile,
    discovery_fingerprint: fingerprint,
    state,
    revision: count(object.revision, status),
    retry_target: retryTarget,
    created_at: timestampString(object.created_at, status),
    updated_at: timestampString(object.updated_at, status),
  };
}

function parseResearch(
  payload: unknown,
  status: number,
  withHistory: boolean,
): ResearchResponse {
  const required = [
    "schema_version",
    "session",
    "assessment",
    "candidates",
    "required_user_action",
    "error_code",
    "acquisition_history",
    "acquisition_history_truncated",
  ];
  const object = record(payload, status);
  requireExactKeys(
    object,
    withHistory ? [...required, "history"] : required,
    status,
  );
  requireSchemaVersion(object.schema_version, status);
  const action = requiredAction(object.required_user_action, status);
  const assessment =
    object.assessment === null
      ? null
      : parseAssessment(object.assessment, status);
  const candidates =
    object.candidates === null
      ? null
      : arrayValue(object.candidates, status, MAX_RESEARCH_CANDIDATES).map(
          (item) => parseCandidate(item, status),
        );
  const history = withHistory
    ? parseTimeline(object.history, status)
    : undefined;
  const session = parseSession(object.session, status);
  requireActionMatchesState(session.state, action, status);
  const response = {
    schema_version: 1 as const,
    session,
    assessment,
    candidates,
    required_user_action: action,
    error_code:
      object.error_code === null
        ? null
        : enumValue(object.error_code, RESEARCH_ERROR_CODES, status),
    acquisition_history: arrayValue(
      object.acquisition_history,
      status,
      MAX_ACQUISITION_ATTEMPTS,
    ).map((item) => parseAcquisitionHistory(item, status)),
    acquisition_history_truncated: booleanValue(
      object.acquisition_history_truncated,
      status,
    ),
    ...(history === undefined ? {} : { history }),
  };
  return response as ResearchResponse;
}

function parseAssessment(value: unknown, status: number): ResearchAssessment {
  const object = record(value, status);
  requireExactKeys(
    object,
    ["created_at", "snapshot", "coverage", "freshness", "passages", "videos"],
    status,
  );
  const snapshot = record(object.snapshot, status);
  requireExactKeys(
    snapshot,
    ["search_generation", "catalog_generation"],
    status,
  );
  const coverage = record(object.coverage, status);
  requireExactKeys(
    coverage,
    [
      "matched_passages",
      "matched_videos",
      "distinct_channels",
      "queries_with_zero_hits",
      "newest_source_published_at",
      "unknown_publication_date_count",
    ],
    status,
  );
  const freshness = record(object.freshness, status);
  requireExactKeys(
    freshness,
    [
      "profile",
      "maximum_age_days",
      "last_successful_discovery_at",
      "stale",
      "reason",
    ],
    status,
  );
  const passages = arrayValue(
    object.passages,
    status,
    MAX_RESEARCH_EVIDENCE,
  ).map((item) => parsePassage(item, status));
  const videos = arrayValue(object.videos, status, MAX_RESEARCH_EVIDENCE).map(
    (item) => parseVideoEvidence(item, status),
  );
  const matchedPassages = boundedCount(
    coverage.matched_passages,
    status,
    MAX_RESEARCH_EVIDENCE,
  );
  const matchedVideos = boundedCount(
    coverage.matched_videos,
    status,
    MAX_RESEARCH_EVIDENCE * 2,
  );
  const distinctChannels = boundedCount(
    coverage.distinct_channels,
    status,
    matchedVideos,
  );
  const unknownPublicationDates = boundedCount(
    coverage.unknown_publication_date_count,
    status,
    videos.length,
  );
  if (
    matchedPassages !== passages.length ||
    unknownPublicationDates !==
      videos.filter((video) => video.published_at === null).length
  ) {
    throw unexpected(status);
  }
  const profile = enumValue(freshness.profile, FRESHNESS_PROFILES, status);
  const maximumAge = nullableCount(freshness.maximum_age_days, status);
  const expectedMaximumAge = {
    fast: 14,
    standard: 30,
    stable: 90,
    historical: null,
  }[profile];
  if (maximumAge !== expectedMaximumAge) throw unexpected(status);
  return {
    created_at: timestampString(object.created_at, status),
    snapshot: {
      search_generation: exactSha256(snapshot.search_generation, status),
      catalog_generation: exactSha256(snapshot.catalog_generation, status),
    },
    coverage: {
      matched_passages: matchedPassages,
      matched_videos: matchedVideos,
      distinct_channels: distinctChannels,
      queries_with_zero_hits: stringArray(
        coverage.queries_with_zero_hits,
        status,
        MAX_RESEARCH_QUERIES,
        500,
      ),
      newest_source_published_at: nullableDate(
        coverage.newest_source_published_at,
        status,
      ),
      unknown_publication_date_count: unknownPublicationDates,
    },
    freshness: {
      profile,
      maximum_age_days: maximumAge,
      last_successful_discovery_at: nullableTimestamp(
        freshness.last_successful_discovery_at,
        status,
      ),
      stale: booleanValue(freshness.stale, status),
      reason: nonEmptyString(freshness.reason, status, 500),
    },
    passages,
    videos,
  };
}

function parsePassage(
  value: unknown,
  status: number,
): ResearchAssessment["passages"][number] {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "query",
      "passage_id",
      "video_id",
      "channel_id",
      "rank",
      "url",
      "excerpt",
      "source_sha256",
    ],
    status,
  );
  const hash = exactSha256(object.source_sha256, status);
  return {
    query: nonEmptyString(object.query, status, 500),
    passage_id: exactSha256(object.passage_id, status),
    video_id: exactVideoId(object.video_id, status),
    channel_id: stringValue(object.channel_id, status, 300),
    rank: positiveSafeInteger(object.rank, status, MAX_RESEARCH_EVIDENCE),
    url: stringValue(object.url, status, 2_048),
    excerpt: nonEmptyString(object.excerpt, status, 1_500),
    source_sha256: hash,
  };
}

function parseVideoEvidence(
  value: unknown,
  status: number,
): ResearchAssessment["videos"][number] {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "query",
      "video_id",
      "source_keys",
      "title",
      "published_at",
      "rank",
      "watch_url",
    ],
    status,
  );
  return {
    query: nonEmptyString(object.query, status, 500),
    video_id: exactVideoId(object.video_id, status),
    source_keys: stringArray(object.source_keys, status, 10, 200),
    title: nonEmptyString(object.title, status, 1_000),
    published_at: nullableDate(object.published_at, status),
    rank: finiteNumber(object.rank, status),
    watch_url: canonicalWatchUrl(
      object.watch_url,
      exactVideoId(object.video_id, status),
      status,
    ),
  };
}

function parseCandidate(value: unknown, status: number): ResearchCandidate {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "video_id",
      "title",
      "channel_id",
      "channel_title",
      "published_at",
      "watch_url",
      "matched_queries",
      "original_rank",
      "status",
    ],
    status,
  );
  return {
    video_id: exactVideoId(object.video_id, status),
    title: stringValue(object.title, status, 1_000),
    channel_id: nullableString(object.channel_id, status, 300),
    channel_title: nullableString(object.channel_title, status, 300),
    published_at: nullableDate(object.published_at, status),
    watch_url: canonicalWatchUrl(
      object.watch_url,
      exactVideoId(object.video_id, status),
      status,
    ),
    matched_queries: stringArray(
      object.matched_queries,
      status,
      MAX_RESEARCH_QUERIES,
      500,
    ),
    original_rank: positiveSafeInteger(
      object.original_rank,
      status,
      MAX_RESEARCH_CANDIDATES,
    ),
    status: enumValue(object.status, CANDIDATE_STATUSES, status),
  };
}

function parseAcquisitionHistory(
  value: unknown,
  status: number,
): AcquisitionHistoryAttempt {
  const object = record(value, status);
  requireExactKeys(object, ["attempt_id", "status", "items"], status);
  return {
    attempt_id: nonEmptyString(object.attempt_id, status, 200),
    status: enumValue(object.status, ACQUISITION_ATTEMPT_STATUSES, status),
    items: arrayValue(object.items, status, 5).map((item) =>
      parseAcquisitionOutcome(item, status),
    ),
  };
}

function parseAcquisitionOutcome(
  value: unknown,
  status: number,
): SourceAcquisitionResult["items"][number] {
  const object = record(value, status);
  requireExactKeys(
    object,
    ["video_id", "status", "error_code", "source_sha256"],
    status,
  );
  const itemStatus = enumValue(
    object.status,
    ACQUISITION_ITEM_STATUSES,
    status,
  );
  const errorCode =
    object.error_code === null
      ? null
      : enumValue(object.error_code, ACQUISITION_ERROR_CODES, status);
  const hash =
    object.source_sha256 === null
      ? null
      : exactSha256(object.source_sha256, status);
  if (
    ((itemStatus === "acquired" || itemStatus === "already_present") &&
      (errorCode !== null || hash === null)) ||
    (itemStatus === "no_transcript" &&
      (errorCode !== "no_transcript" || hash !== null)) ||
    (itemStatus === "failed_retryable" &&
      (errorCode === null || errorCode === "no_transcript"))
  ) {
    throw unexpected(status);
  }
  return {
    video_id: exactVideoId(object.video_id, status),
    status: itemStatus,
    error_code: errorCode,
    source_sha256: hash,
  };
}

function parseTimeline(value: unknown, status: number): ResearchTimeline {
  const object = record(value, status);
  requireExactKeys(
    object,
    ["decisions", "events", "decisions_truncated", "events_truncated"],
    status,
  );
  return {
    decisions: arrayValue(object.decisions, status, MAX_TIMELINE_ITEMS).map(
      (item) => {
        const decision = record(item, status);
        requireExactKeys(decision, ["action", "created_at"], status);
        return {
          action: nonEmptyString(decision.action, status, 500),
          created_at: timestampString(decision.created_at, status),
        };
      },
    ),
    events: arrayValue(object.events, status, MAX_TIMELINE_ITEMS).map(
      (item) => {
        const event = record(item, status);
        requireExactKeys(
          event,
          ["event_id", "from_state", "to_state", "event_code", "created_at"],
          status,
        );
        return {
          event_id: count(event.event_id, status),
          from_state:
            event.from_state === null
              ? null
              : enumValue(event.from_state, RESEARCH_STATES, status),
          to_state: enumValue(event.to_state, RESEARCH_STATES, status),
          event_code: nonEmptyString(event.event_code, status, 500),
          created_at: timestampString(event.created_at, status),
        };
      },
    ),
    decisions_truncated: booleanValue(object.decisions_truncated, status),
    events_truncated: booleanValue(object.events_truncated, status),
  };
}

function parseExports(payload: unknown, status: number): ExportsResponse {
  const object = versioned(
    payload,
    [
      "items",
      "limit",
      "truncated",
      "inventory_complete",
      "inventory_examined",
      "inventory_limit",
    ],
    status,
  );
  const limit = pageLimit(object.limit, status);
  const items = arrayValue(object.items, status, limit).map((item) =>
    parseExportItem(item, status),
  );
  const truncated = booleanValue(object.truncated, status);
  const inventoryComplete = booleanValue(object.inventory_complete, status);
  const inventoryLimit = boundedCount(object.inventory_limit, status, 32);
  const inventoryExamined = boundedCount(
    object.inventory_examined,
    status,
    inventoryLimit,
  );
  if (inventoryLimit !== 32 || (!inventoryComplete && !truncated)) {
    throw unexpected(status);
  }
  return {
    schema_version: 1,
    items,
    limit,
    truncated,
    inventory_complete: inventoryComplete,
    inventory_examined: inventoryExamined,
    inventory_limit: inventoryLimit,
  };
}

function parseExportItem(value: unknown, status: number): ExportItem {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "name",
      "session_id",
      "created_at",
      "manifest_valid",
      "export_id",
      "open_url",
    ],
    status,
  );
  const exportId = exactSha256(object.export_id, status);
  const openUrl = nullableString(object.open_url, status, 110);
  const match = openUrl?.match(EXPORT_OPEN_URL) ?? null;
  if (openUrl !== null && match?.[1] !== exportId) throw unexpected(status);
  const manifestValid = booleanValue(object.manifest_valid, status);
  if (manifestValid !== (openUrl !== null)) throw unexpected(status);
  const sessionId = nullableString(object.session_id, status, 128);
  if (sessionId !== null && !SESSION_ID.test(sessionId))
    throw unexpected(status);
  const createdAt = nullableTimestamp(object.created_at, status);
  if (
    manifestValid !== (sessionId !== null && createdAt !== null) ||
    (!manifestValid && (sessionId !== null || createdAt !== null))
  ) {
    throw unexpected(status);
  }
  return {
    name: nonEmptyString(object.name, status, 255),
    session_id: sessionId,
    created_at: createdAt,
    manifest_valid: manifestValid,
    export_id: exportId,
    open_url: openUrl,
  };
}

function parseJobResponse(payload: unknown, status: number): JobResponse {
  const object = versioned(payload, ["job"], status);
  return { schema_version: 1, job: parseJob(object.job, status) };
}

function parseJob(value: unknown, status: number): Job {
  const object = record(value, status);
  requireExactKeys(
    object,
    ["job_id", "kind", "status", "result", "error_code"],
    status,
  );
  const jobId = stringValue(object.job_id, status, 200);
  if (!JOB_ID.test(jobId)) throw unexpected(status);
  const kind = enumValue(object.kind, JOB_KINDS, status);
  if (object.status === "queued" || object.status === "running") {
    if (object.result !== null || object.error_code !== null)
      throw unexpected(status);
    return {
      job_id: jobId,
      kind,
      status: object.status,
      result: null,
      error_code: null,
    };
  }
  if (object.status === "failed") {
    if (object.result !== null || object.error_code !== "operation_failed")
      throw unexpected(status);
    return {
      job_id: jobId,
      kind,
      status: "failed",
      result: null,
      error_code: "operation_failed",
    };
  }
  if (
    object.status !== "succeeded" ||
    object.error_code !== null ||
    object.result === null
  ) {
    throw unexpected(status);
  }
  return {
    job_id: jobId,
    kind,
    status: "succeeded",
    result: parseJobResult(kind, object.result, status),
    error_code: null,
  };
}

function parseJobResult(
  kind: JobKind,
  value: unknown,
  status: number,
): Job["result"] & object {
  if (
    isRecord(value) &&
    Object.keys(value).length === 1 &&
    value.truncated === true
  ) {
    return { truncated: true };
  }
  const possibleError = tryJobResultError(value, status);
  if (possibleError !== null) return possibleError;
  if (kind === "source_preview") return parseSourcePreview(value, status);
  if (kind === "source_acquisition")
    return parseSourceAcquisition(value, status);
  return parseResearch(value, status, false);
}

function tryJobResultError(
  value: unknown,
  status: number,
): JobResultError | null {
  if (!isRecord(value) || !("error" in value)) return null;
  const object = record(value, status);
  requireExactKeys(object, ["schema_version", "error"], status);
  requireSchemaVersion(object.schema_version, status);
  const error = record(object.error, status);
  requireExactKeys(error, ["code"], status);
  const code = stringValue(error.code, status);
  if (!JOB_RESULT_ERROR_CODES.has(code as never)) throw unexpected(status);
  return {
    schema_version: 1,
    error: { code: code as JobResultError["error"]["code"] },
  };
}

function parseSourcePreview(
  value: unknown,
  status: number,
): SourcePreviewResult {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "fingerprint",
      "source_kind",
      "selected_count",
      "video_ids",
      "videos",
      "videos_returned",
      "videos_truncated",
      "language",
      "analyze",
      "requires_confirmation",
      "excluded_count",
      "discovery_error_count",
    ],
    status,
  );
  const fingerprint = exactSha256(object.fingerprint, status);
  const videoIds = arrayValue(
    object.video_ids,
    status,
    MAX_SELECTED_VIDEOS,
  ).map((item) => exactVideoId(item, status));
  if (new Set(videoIds).size !== videoIds.length) throw unexpected(status);
  const videos = arrayValue(object.videos, status, MAX_SELECTED_VIDEOS).map(
    (item) => {
      const video = record(item, status);
      requireExactKeys(
        video,
        ["video_id", "title", "published_at", "url"],
        status,
      );
      const videoId = exactVideoId(video.video_id, status);
      if (!videoIds.includes(videoId)) throw unexpected(status);
      return {
        video_id: videoId,
        title: stringValue(video.title, status, 300),
        published_at: sourcePreviewDate(video.published_at, status),
        url: canonicalWatchUrl(video.url, videoId, status),
      };
    },
  );
  const sourceKind = enumValue(object.source_kind, SOURCE_KINDS, status);
  const selectedCount = boundedCount(
    object.selected_count,
    status,
    MAX_SELECTED_VIDEOS,
  );
  const videosTruncated = booleanValue(object.videos_truncated, status);
  const requiresConfirmation = booleanValue(
    object.requires_confirmation,
    status,
  );
  const language = nonEmptyString(object.language, status, 500);
  if (
    selectedCount !== videoIds.length ||
    count(object.videos_returned, status) !== videos.length ||
    new Set(videos.map((video) => video.video_id)).size !== videos.length ||
    videos.some((video, index) => video.video_id !== videoIds[index]) ||
    (!videosTruncated && videos.length !== selectedCount) ||
    (videosTruncated && videos.length >= selectedCount) ||
    videos.length > selectedCount ||
    requiresConfirmation !== (sourceKind !== "video") ||
    !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(language)
  ) {
    throw unexpected(status);
  }
  return {
    fingerprint,
    source_kind: sourceKind,
    selected_count: selectedCount,
    video_ids: videoIds,
    videos,
    videos_returned: videos.length,
    videos_truncated: videosTruncated,
    language,
    analyze: booleanValue(object.analyze, status),
    requires_confirmation: requiresConfirmation,
    excluded_count: boundedCount(
      object.excluded_count,
      status,
      MAX_PLAN_IDENTITY_BYTES,
    ),
    discovery_error_count: boundedCount(
      object.discovery_error_count,
      status,
      MAX_PLAN_IDENTITY_BYTES,
    ),
  };
}

function parseSourceAcquisition(
  value: unknown,
  status: number,
): SourceAcquisitionResult {
  const object = record(value, status);
  requireExactKeys(
    object,
    [
      "selected",
      "transcripts_ready",
      "insights_ready",
      "failure_count",
      "exclusion_count",
      "items",
      "exit_code",
    ],
    status,
  );
  const selected = boundedCount(object.selected, status, MAX_ACQUISITION_ITEMS);
  const transcriptsReady = boundedCount(
    object.transcripts_ready,
    status,
    selected,
  );
  const insightsReady = boundedCount(
    object.insights_ready,
    status,
    transcriptsReady,
  );
  const failureCount = boundedCount(
    object.failure_count,
    status,
    MAX_ACQUISITION_DIAGNOSTICS,
  );
  const exclusionCount = boundedCount(
    object.exclusion_count,
    status,
    MAX_PLAN_IDENTITY_BYTES,
  );
  const items = arrayValue(object.items, status, MAX_ACQUISITION_ITEMS).map(
    (item) => parseAcquisitionOutcome(item, status),
  );
  const minimumFailures = items.filter(
    (item) => item.error_code !== null,
  ).length;
  const readyItems = items.filter((item) => item.source_sha256 !== null).length;
  const exitCode = count(object.exit_code, status);
  const expectedExitCode =
    selected > 0 && transcriptsReady === 0
      ? 1
      : failureCount > 0 || transcriptsReady < selected
        ? transcriptsReady > 0
          ? 4
          : 1
        : 0;
  if (
    items.length !== selected ||
    readyItems !== transcriptsReady ||
    failureCount < minimumFailures ||
    exitCode !== expectedExitCode
  ) {
    throw unexpected(status);
  }
  return {
    selected,
    transcripts_ready: transcriptsReady,
    insights_ready: insightsReady,
    failure_count: failureCount,
    exclusion_count: exclusionCount,
    items,
    exit_code: exitCode,
  };
}

function parseJobAccepted(
  payload: unknown,
  status: number,
): JobAcceptedResponse {
  const object = versioned(payload, ["job_id"], status);
  const jobId = stringValue(object.job_id, status, 200);
  if (!JOB_ID.test(jobId)) throw unexpected(status);
  return { schema_version: 1, job_id: jobId };
}

function parseExportCreated(
  payload: unknown,
  status: number,
): ExportCreatedResponse {
  const object = versioned(payload, ["export"], status);
  const result = record(object.export, status);
  requireExactKeys(
    result,
    ["name", "manifest_sha256", "dossier_sha256"],
    status,
  );
  const manifest = exactSha256(result.manifest_sha256, status);
  const dossier = exactSha256(result.dossier_sha256, status);
  return {
    schema_version: 1,
    export: {
      name: nonEmptyString(result.name, status, 255),
      manifest_sha256: manifest,
      dossier_sha256: dossier,
    },
  };
}

function requireApiPath(path: string, method: "GET" | "POST"): string {
  if (
    !path.startsWith("/api/v1/") ||
    path.startsWith("//") ||
    path.includes("#")
  ) {
    throw new TypeError("API path is invalid");
  }
  let parsed: URL;
  try {
    parsed = new URL(path, "http://local.invalid");
  } catch {
    throw new TypeError("API path is invalid");
  }
  if (parsed.origin !== "http://local.invalid")
    throw new TypeError("API path is invalid");
  const route = parsed.pathname;
  const getRoute =
    route === "/api/v1/status" ||
    route === "/api/v1/search" ||
    route === "/api/v1/sources" ||
    route === "/api/v1/research/sessions" ||
    route === "/api/v1/exports" ||
    /^\/api\/v1\/research\/sessions\/[A-Za-z0-9_-]{1,128}$/.test(route) ||
    /^\/api\/v1\/jobs\/[A-Za-z0-9_-]{1,200}$/.test(route);
  const postRoute =
    route === "/api/v1/sources/preview" ||
    route === "/api/v1/sources/acquire" ||
    route === "/api/v1/research/sessions" ||
    /^\/api\/v1\/research\/sessions\/[A-Za-z0-9_-]{1,128}\/(decisions|discovery|approvals|acquisition|retry|exports)$/.test(
      route,
    );
  if ((method === "GET" && !getRoute) || (method === "POST" && !postRoute)) {
    throw new TypeError("API path is invalid");
  }
  return route;
}

function versioned(
  value: unknown,
  fields: readonly string[],
  status: number,
): Record<string, unknown> {
  const object = record(value, status);
  requireExactKeys(object, ["schema_version", ...fields], status);
  requireSchemaVersion(object.schema_version, status);
  return object;
}

function record(value: unknown, status: number): Record<string, unknown> {
  if (!isRecord(value)) throw unexpected(status);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireExactKeys(
  object: Record<string, unknown>,
  expected: readonly string[],
  status: number,
): void {
  const keys = Object.keys(object);
  if (
    keys.length !== expected.length ||
    expected.some((key) => !(key in object))
  ) {
    throw unexpected(status);
  }
}

function requireSchemaVersion(value: unknown, status: number): void {
  if (value !== 1) throw unexpected(status);
}

function arrayValue(
  value: unknown,
  status: number,
  maximumItems: number,
): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximumItems)
    throw unexpected(status);
  return value;
}

function stringArray(
  value: unknown,
  status: number,
  maximumItems: number,
  maximumCodePoints = MAX_PUBLIC_STRING_CODEPOINTS,
): readonly string[] {
  return arrayValue(value, status, maximumItems).map((item) =>
    stringValue(item, status, maximumCodePoints),
  );
}

function stringValue(
  value: unknown,
  status: number,
  maximumCodePoints = MAX_PUBLIC_STRING_CODEPOINTS,
): string {
  if (
    typeof value !== "string" ||
    unicodeCodePointLength(value, maximumCodePoints) > maximumCodePoints
  )
    throw unexpected(status);
  return value;
}

function unicodeCodePointLength(value: string, stopAfter: number): number {
  let count = 0;
  for (const _character of value) {
    count += 1;
    if (count > stopAfter) return count;
  }
  return count;
}

function nullableString(
  value: unknown,
  status: number,
  maximumCodePoints = MAX_PUBLIC_STRING_CODEPOINTS,
): string | null {
  return value === null ? null : stringValue(value, status, maximumCodePoints);
}

function booleanValue(value: unknown, status: number): boolean {
  if (typeof value !== "boolean") throw unexpected(status);
  return value;
}

function finiteNumber(value: unknown, status: number): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw unexpected(status);
  return value;
}

function count(value: unknown, status: number): number {
  const parsed = finiteNumber(value, status);
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw unexpected(status);
  return parsed;
}

function boundedCount(value: unknown, status: number, maximum: number): number {
  const parsed = count(value, status);
  if (parsed > maximum) throw unexpected(status);
  return parsed;
}

function positiveSafeInteger(
  value: unknown,
  status: number,
  maximum: number,
): number {
  const parsed = boundedCount(value, status, maximum);
  if (parsed < 1) throw unexpected(status);
  return parsed;
}

function nullableCount(value: unknown, status: number): number | null {
  return value === null ? null : count(value, status);
}

function enumValue<T extends string>(
  value: unknown,
  allowed: ReadonlySet<T>,
  status: number,
): T {
  const parsed = stringValue(value, status);
  if (!allowed.has(parsed as T)) throw unexpected(status);
  return parsed as T;
}

function requiredAction(
  value: unknown,
  status: number,
): "confirm_sufficiency_or_refresh" | "approve_candidates_or_cancel" | null {
  if (value === null) return null;
  return enumValue(value, REQUIRED_ACTIONS, status);
}

function requireActionMatchesState(
  state: ResearchState,
  action:
    "confirm_sufficiency_or_refresh" | "approve_candidates_or_cancel" | null,
  status: number,
): void {
  const expected =
    state === "awaiting_sufficiency_confirmation"
      ? "confirm_sufficiency_or_refresh"
      : state === "awaiting_candidate_approval"
        ? "approve_candidates_or_cancel"
        : null;
  if (action !== expected) throw unexpected(status);
}

function exactVideoId(value: unknown, status: number): string {
  const parsed = stringValue(value, status);
  if (!VIDEO_ID.test(parsed)) throw unexpected(status);
  return parsed;
}

function exactSha256(value: unknown, status: number): string {
  const parsed = stringValue(value, status, 64);
  if (!SHA256.test(parsed)) throw unexpected(status);
  return parsed;
}

function nonEmptyString(
  value: unknown,
  status: number,
  maximumCodePoints: number,
): string {
  const parsed = stringValue(value, status, maximumCodePoints);
  if (parsed.trim() === "") throw unexpected(status);
  return parsed;
}

function canonicalWatchUrl(
  value: unknown,
  videoId: string,
  status: number,
): string {
  const parsed = stringValue(value, status, 2_048);
  if (parsed !== `https://www.youtube.com/watch?v=${videoId}`)
    throw unexpected(status);
  return parsed;
}

function nullableDate(value: unknown, status: number): string | null {
  if (value === null) return null;
  const parsed = stringValue(value, status, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(parsed)) throw unexpected(status);
  const date = new Date(`${parsed}T00:00:00Z`);
  if (
    !Number.isFinite(date.valueOf()) ||
    date.toISOString().slice(0, 10) !== parsed
  ) {
    throw unexpected(status);
  }
  return parsed;
}

function sourcePreviewDate(value: unknown, status: number): string {
  if (value === "unknown") return "unknown";
  const parsed = nullableDate(value, status);
  if (parsed === null) throw unexpected(status);
  return parsed;
}

function timestampString(value: unknown, status: number): string {
  const parsed = nonEmptyString(value, status, 64);
  if (!Number.isFinite(Date.parse(parsed))) throw unexpected(status);
  return parsed;
}

function nullableTimestamp(value: unknown, status: number): string | null {
  return value === null ? null : timestampString(value, status);
}

function pageLimit(value: unknown, status: number): number {
  return positiveSafeInteger(value, status, MAX_PAGE_SIZE);
}

function isPublicErrorCode(
  value: string,
): value is Exclude<PublicApiErrorCode, "unexpected_response"> {
  return PUBLIC_ERROR_CODES.has(value);
}

function isAbortError(value: unknown): value is Error {
  return value instanceof Error && value.name === "AbortError";
}

function unexpected(status: number): PublicApiError {
  return new PublicApiError("unexpected_response", status);
}

function publicErrorMessage(code: PublicApiErrorCode): string {
  const messages: Record<PublicApiErrorCode, string> = {
    unexpected_response: "The local server returned an unexpected response.",
    invalid_request: "The request is invalid.",
    forbidden: "The local server rejected this request.",
    method_not_allowed: "This operation is not supported.",
    not_found: "The requested item was not found.",
    plan_changed: "The source plan changed. Preview it again before acquiring.",
    stale_revision: "The session changed. Review it before deciding again.",
    workflow_conflict:
      "This action is not available in the current session state.",
    idempotency_conflict:
      "This request key was already used for different input.",
    request_in_progress: "The same request is still in progress.",
    job_queue_full:
      "The local work queue is full. Try again after a job completes.",
    jobs_unavailable: "Background jobs are unavailable.",
    search_unavailable: "Corpus search is unavailable.",
    catalog_unavailable: "The source catalog is unavailable.",
    research_unavailable: "Research data is unavailable.",
    exports_unavailable: "Exports are unavailable.",
    internal_error: "The local server could not complete the request.",
    server_busy: "The local server is busy. Try again shortly.",
    server_shutting_down: "The local server is shutting down.",
  };
  return messages[code];
}
