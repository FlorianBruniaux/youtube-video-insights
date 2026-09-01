import type {
  AcquisitionHistoryAttempt,
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
  ResearchResponse,
  ResearchSessionCore,
  ResearchSessionSummary,
  ResearchState,
  ResearchTimeline,
  SearchHit,
  SearchResponse,
  SourceAcquisitionResult,
  SourceItem,
  SourcePreviewResult,
  SourcesResponse,
  StatusResponse,
} from "./types";

const MUTATION_TOKEN_HEADER = "X-YT-Insights-Token";
const PUBLIC_ERROR_CODES = new Set<PublicApiErrorCode>([
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
  if (!response.ok) throw publicResponseError(payload, response.status);
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
  if (!response.ok) throw publicResponseError(payload, response.status);
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
    throw unexpected(response.status);
  }
  try {
    return await response.json();
  } catch {
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
  if (!PUBLIC_ERROR_CODES.has(code as PublicApiErrorCode))
    throw unexpected(status);
  return new PublicApiError(code as PublicApiErrorCode, status);
}

function parseBootstrap(payload: unknown, status: number): BootstrapResponse {
  const object = record(payload, status);
  requireExactKeys(object, ["schema_version", "mutation_token"], status);
  requireSchemaVersion(object.schema_version, status);
  const token = stringValue(object.mutation_token, status);
  if (token.length < 32 || token.length > 500) throw unexpected(status);
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
  const hits = arrayValue(object.hits, status).map((item) =>
    parseSearchHit(item, status),
  );
  const returned = count(object.returned, status);
  if (returned !== hits.length) throw unexpected(status);
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
  return {
    passage_id: stringValue(object.passage_id, status),
    rank: finiteNumber(object.rank, status),
    score: finiteNumber(object.score, status),
    channel_id: stringValue(object.channel_id, status),
    channel: stringValue(object.channel, status),
    title: stringValue(object.title, status),
    language: stringValue(object.language, status),
    excerpt: stringValue(object.excerpt, status),
    start_seconds: finiteNumber(object.start_seconds, status),
    end_seconds: finiteNumber(object.end_seconds, status),
    url: stringValue(object.url, status),
  };
}

function parseSources(payload: unknown, status: number): SourcesResponse {
  const object = versioned(payload, ["items", "limit", "offset"], status);
  return {
    schema_version: 1,
    items: arrayValue(object.items, status).map((item) =>
      parseSource(item, status),
    ),
    limit: count(object.limit, status),
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
    title: stringValue(object.title, status),
    published_at: nullableString(object.published_at, status),
    languages: stringArray(object.languages, status),
    sources: stringArray(object.sources, status),
    url: stringValue(object.url, status),
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
  return {
    schema_version: 1,
    items: arrayValue(object.items, status).map((item) =>
      parseSessionSummary(item, status),
    ),
    limit: count(object.limit, status),
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
  const fingerprint = stringValue(object.discovery_fingerprint, status);
  if (!SHA256.test(fingerprint)) throw unexpected(status);
  const sessionId = stringValue(object.session_id, status);
  if (!SESSION_ID.test(sessionId)) throw unexpected(status);
  return {
    session_id: sessionId,
    topic: stringValue(object.topic, status),
    queries: stringArray(object.queries, status),
    languages: stringArray(object.languages, status),
    freshness_profile: profile,
    discovery_fingerprint: fingerprint,
    state,
    revision: count(object.revision, status),
    retry_target: retryTarget,
    created_at: stringValue(object.created_at, status),
    updated_at: stringValue(object.updated_at, status),
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
      : arrayValue(object.candidates, status).map((item) =>
          parseCandidate(item, status),
        );
  const history = withHistory
    ? parseTimeline(object.history, status)
    : undefined;
  const response = {
    schema_version: 1 as const,
    session: parseSession(object.session, status),
    assessment,
    candidates,
    required_user_action: action,
    error_code: nullableString(object.error_code, status),
    acquisition_history: arrayValue(object.acquisition_history, status).map(
      (item) => parseAcquisitionHistory(item, status),
    ),
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
  return {
    created_at: stringValue(object.created_at, status),
    snapshot: {
      search_generation: stringValue(snapshot.search_generation, status),
      catalog_generation: stringValue(snapshot.catalog_generation, status),
    },
    coverage: {
      matched_passages: count(coverage.matched_passages, status),
      matched_videos: count(coverage.matched_videos, status),
      distinct_channels: count(coverage.distinct_channels, status),
      queries_with_zero_hits: stringArray(
        coverage.queries_with_zero_hits,
        status,
      ),
      newest_source_published_at: nullableString(
        coverage.newest_source_published_at,
        status,
      ),
      unknown_publication_date_count: count(
        coverage.unknown_publication_date_count,
        status,
      ),
    },
    freshness: {
      profile: enumValue(freshness.profile, FRESHNESS_PROFILES, status),
      maximum_age_days: nullableCount(freshness.maximum_age_days, status),
      last_successful_discovery_at: nullableString(
        freshness.last_successful_discovery_at,
        status,
      ),
      stale: booleanValue(freshness.stale, status),
      reason: stringValue(freshness.reason, status),
    },
    passages: arrayValue(object.passages, status).map((item) =>
      parsePassage(item, status),
    ),
    videos: arrayValue(object.videos, status).map((item) =>
      parseVideoEvidence(item, status),
    ),
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
  const hash = stringValue(object.source_sha256, status);
  if (!SHA256.test(hash)) throw unexpected(status);
  return {
    query: stringValue(object.query, status),
    passage_id: stringValue(object.passage_id, status),
    video_id: exactVideoId(object.video_id, status),
    channel_id: stringValue(object.channel_id, status),
    rank: finiteNumber(object.rank, status),
    url: stringValue(object.url, status),
    excerpt: stringValue(object.excerpt, status),
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
    query: stringValue(object.query, status),
    video_id: exactVideoId(object.video_id, status),
    source_keys: stringArray(object.source_keys, status),
    title: stringValue(object.title, status),
    published_at: nullableString(object.published_at, status),
    rank: finiteNumber(object.rank, status),
    watch_url: stringValue(object.watch_url, status),
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
    title: stringValue(object.title, status),
    channel_id: nullableString(object.channel_id, status),
    channel_title: nullableString(object.channel_title, status),
    published_at: nullableString(object.published_at, status),
    watch_url: stringValue(object.watch_url, status),
    matched_queries: stringArray(object.matched_queries, status),
    original_rank: finiteNumber(object.original_rank, status),
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
    attempt_id: stringValue(object.attempt_id, status),
    status: stringValue(object.status, status),
    items: arrayValue(object.items, status).map((item) => {
      const outcome = record(item, status);
      requireExactKeys(
        outcome,
        ["video_id", "status", "error_code", "source_sha256"],
        status,
      );
      const hash = nullableString(outcome.source_sha256, status);
      if (hash !== null && !SHA256.test(hash)) throw unexpected(status);
      return {
        video_id: exactVideoId(outcome.video_id, status),
        status: enumValue(outcome.status, CANDIDATE_STATUSES, status),
        error_code: nullableString(outcome.error_code, status),
        source_sha256: hash,
      };
    }),
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
    decisions: arrayValue(object.decisions, status).map((item) => {
      const decision = record(item, status);
      requireExactKeys(decision, ["action", "created_at"], status);
      return {
        action: stringValue(decision.action, status),
        created_at: stringValue(decision.created_at, status),
      };
    }),
    events: arrayValue(object.events, status).map((item) => {
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
        event_code: stringValue(event.event_code, status),
        created_at: stringValue(event.created_at, status),
      };
    }),
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
  return {
    schema_version: 1,
    items: arrayValue(object.items, status).map((item) =>
      parseExportItem(item, status),
    ),
    limit: count(object.limit, status),
    truncated: booleanValue(object.truncated, status),
    inventory_complete: booleanValue(object.inventory_complete, status),
    inventory_examined: count(object.inventory_examined, status),
    inventory_limit: count(object.inventory_limit, status),
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
  const exportId = stringValue(object.export_id, status);
  if (!SHA256.test(exportId)) throw unexpected(status);
  const openUrl = nullableString(object.open_url, status);
  const match = openUrl?.match(EXPORT_OPEN_URL) ?? null;
  if (openUrl !== null && match?.[1] !== exportId) throw unexpected(status);
  const manifestValid = booleanValue(object.manifest_valid, status);
  if (manifestValid !== (openUrl !== null)) throw unexpected(status);
  return {
    name: stringValue(object.name, status),
    session_id: nullableString(object.session_id, status),
    created_at: nullableString(object.created_at, status),
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
  const jobId = stringValue(object.job_id, status);
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
  if (isRecord(value) && Object.keys(value).length === 1 && value.truncated === true) {
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
  const fingerprint = stringValue(object.fingerprint, status);
  if (!SHA256.test(fingerprint)) throw unexpected(status);
  const videoIds = arrayValue(object.video_ids, status).map((item) =>
    exactVideoId(item, status),
  );
  const videos = arrayValue(object.videos, status).map((item) => {
    const video = record(item, status);
    requireExactKeys(
      video,
      ["video_id", "title", "published_at", "url"],
      status,
    );
    return {
      video_id: exactVideoId(video.video_id, status),
      title: stringValue(video.title, status),
      published_at: nullableString(video.published_at, status),
      url: stringValue(video.url, status),
    };
  });
  if (count(object.videos_returned, status) !== videos.length)
    throw unexpected(status);
  return {
    fingerprint,
    source_kind: stringValue(object.source_kind, status),
    selected_count: count(object.selected_count, status),
    video_ids: videoIds,
    videos,
    videos_returned: videos.length,
    videos_truncated: booleanValue(object.videos_truncated, status),
    language: stringValue(object.language, status),
    analyze: booleanValue(object.analyze, status),
    requires_confirmation: booleanValue(object.requires_confirmation, status),
    excluded_count: count(object.excluded_count, status),
    discovery_error_count: count(object.discovery_error_count, status),
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
  return {
    selected: count(object.selected, status),
    transcripts_ready: count(object.transcripts_ready, status),
    insights_ready: count(object.insights_ready, status),
    failure_count: count(object.failure_count, status),
    exclusion_count: count(object.exclusion_count, status),
    items: arrayValue(object.items, status).map((item) => {
      const result = record(item, status);
      requireExactKeys(
        result,
        ["video_id", "status", "error_code", "source_sha256"],
        status,
      );
      const hash = nullableString(result.source_sha256, status);
      if (hash !== null && !SHA256.test(hash)) throw unexpected(status);
      return {
        video_id: exactVideoId(result.video_id, status),
        status: stringValue(result.status, status),
        error_code: nullableString(result.error_code, status),
        source_sha256: hash,
      };
    }),
    exit_code: count(object.exit_code, status),
  };
}

function parseJobAccepted(
  payload: unknown,
  status: number,
): JobAcceptedResponse {
  const object = versioned(payload, ["job_id"], status);
  const jobId = stringValue(object.job_id, status);
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
  const manifest = stringValue(result.manifest_sha256, status);
  const dossier = stringValue(result.dossier_sha256, status);
  if (!SHA256.test(manifest) || !SHA256.test(dossier)) throw unexpected(status);
  return {
    schema_version: 1,
    export: {
      name: stringValue(result.name, status),
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

function arrayValue(value: unknown, status: number): readonly unknown[] {
  if (!Array.isArray(value)) throw unexpected(status);
  return value;
}

function stringArray(value: unknown, status: number): readonly string[] {
  return arrayValue(value, status).map((item) => stringValue(item, status));
}

function stringValue(value: unknown, status: number): string {
  if (typeof value !== "string") throw unexpected(status);
  return value;
}

function nullableString(value: unknown, status: number): string | null {
  return value === null ? null : stringValue(value, status);
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
  if (!Number.isInteger(parsed) || parsed < 0) throw unexpected(status);
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

function exactVideoId(value: unknown, status: number): string {
  const parsed = stringValue(value, status);
  if (!VIDEO_ID.test(parsed)) throw unexpected(status);
  return parsed;
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
