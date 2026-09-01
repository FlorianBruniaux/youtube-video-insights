export type CorpusHealth = "ready" | "partial";

export interface CorpusStatus {
  readonly health: CorpusHealth;
  readonly videos: number;
  readonly transcripts: number;
  readonly documents_indexed: number | null;
  readonly passages_indexed: number | null;
}

export interface StatusResponse {
  readonly schema_version: 1;
  readonly status: "ok";
  readonly corpus: CorpusStatus;
}

export interface SearchHit {
  readonly passage_id: string;
  readonly rank: number;
  readonly score: number;
  readonly channel_id: string;
  readonly channel: string;
  readonly title: string;
  readonly language: string;
  readonly excerpt: string;
  readonly start_seconds: number;
  readonly end_seconds: number;
  readonly url: string;
}

export interface SearchResponse {
  readonly schema_version: 1;
  readonly hits: readonly SearchHit[];
  readonly returned: number;
  readonly truncated: boolean;
}

export type TranscriptState = "available" | "missing";
export type IndexState = "indexed" | "not_indexed" | "unknown";

export interface SourceItem {
  readonly video_id: string;
  readonly title: string;
  readonly published_at: string | null;
  readonly languages: readonly string[];
  readonly sources: readonly string[];
  readonly url: string;
  readonly artifact_count: number;
  readonly transcript_state: TranscriptState;
  readonly index_state: IndexState;
}

export interface SourcesResponse {
  readonly schema_version: 1;
  readonly items: readonly SourceItem[];
  readonly limit: number;
  readonly offset: number;
}

export type FreshnessProfile = "fast" | "standard" | "stable" | "historical";
export type ResearchState =
  | "assessing"
  | "awaiting_sufficiency_confirmation"
  | "discovering"
  | "awaiting_candidate_approval"
  | "acquiring"
  | "reindexing"
  | "completed"
  | "failed_retryable"
  | "cancelled";
export type RequiredUserAction =
  "confirm_sufficiency_or_refresh" | "approve_candidates_or_cancel";
export type CandidateStatus =
  | "candidate"
  | "approved"
  | "acquired"
  | "already_present"
  | "no_transcript"
  | "failed_retryable";
export type SourceKind = "video" | "playlist" | "channel" | "batch";
export type AcquisitionItemStatus =
  "acquired" | "already_present" | "no_transcript" | "failed_retryable";
export type AcquisitionErrorCode =
  | "acquisition_unavailable"
  | "cache_read_failed"
  | "download_failed"
  | "no_transcript"
  | "acquisition_failed";
export type ResearchErrorCode =
  | "acquisition_in_progress"
  | "acquisition_unavailable"
  | "discovery_unavailable"
  | "index_refresh_failed"
  | "local_index_unavailable"
  | "partial_acquisition_failed"
  | "retry_in_progress"
  | "research_unavailable";

export interface ResearchSessionCore {
  readonly session_id: string;
  readonly topic: string;
  readonly queries: readonly string[];
  readonly languages: readonly string[];
  readonly freshness_profile: FreshnessProfile;
  readonly discovery_fingerprint: string;
  readonly state: ResearchState;
  readonly revision: number;
  readonly retry_target: ResearchState | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export type ResearchSessionSummary = ResearchSessionCore &
  (
    | { readonly required_user_action: null }
    | { readonly required_user_action: "confirm_sufficiency_or_refresh" }
    | { readonly required_user_action: "approve_candidates_or_cancel" }
  );

export interface ResearchListResponse {
  readonly schema_version: 1;
  readonly items: readonly ResearchSessionSummary[];
  readonly limit: number;
  readonly offset: number;
}

export interface CoverageMetrics {
  readonly matched_passages: number;
  readonly matched_videos: number;
  readonly distinct_channels: number;
  readonly queries_with_zero_hits: readonly string[];
  readonly newest_source_published_at: string | null;
  readonly unknown_publication_date_count: number;
}

export interface FreshnessAssessment {
  readonly profile: FreshnessProfile;
  readonly maximum_age_days: number | null;
  readonly last_successful_discovery_at: string | null;
  readonly stale: boolean;
  readonly reason: string;
}

export interface PassageEvidence {
  readonly query: string;
  readonly passage_id: string;
  readonly video_id: string;
  readonly channel_id: string;
  readonly rank: number;
  readonly url: string;
  readonly excerpt: string;
  readonly source_sha256: string;
}

export interface VideoEvidence {
  readonly query: string;
  readonly video_id: string;
  readonly source_keys: readonly string[];
  readonly title: string;
  readonly published_at: string | null;
  readonly rank: number;
  readonly watch_url: string;
}

export interface ResearchAssessment {
  readonly created_at: string;
  readonly snapshot: {
    readonly search_generation: string;
    readonly catalog_generation: string;
  };
  readonly coverage: CoverageMetrics;
  readonly freshness: FreshnessAssessment;
  readonly passages: readonly PassageEvidence[];
  readonly videos: readonly VideoEvidence[];
}

export interface ResearchCandidate {
  readonly video_id: string;
  readonly title: string;
  readonly channel_id: string | null;
  readonly channel_title: string | null;
  readonly published_at: string | null;
  readonly watch_url: string;
  readonly matched_queries: readonly string[];
  readonly original_rank: number;
  readonly status: CandidateStatus;
}

export interface AcquisitionHistoryItem {
  readonly video_id: string;
  readonly status: AcquisitionItemStatus;
  readonly error_code: AcquisitionErrorCode | null;
  readonly source_sha256: string | null;
}

export interface AcquisitionHistoryAttempt {
  readonly attempt_id: string;
  readonly status: "running" | "failed_retryable" | "completed";
  readonly items: readonly AcquisitionHistoryItem[];
}

export interface ResearchTimeline {
  readonly decisions: readonly {
    readonly action: string;
    readonly created_at: string;
  }[];
  readonly events: readonly {
    readonly event_id: number;
    readonly from_state: ResearchState | null;
    readonly to_state: ResearchState;
    readonly event_code: string;
    readonly created_at: string;
  }[];
  readonly decisions_truncated: boolean;
  readonly events_truncated: boolean;
}

interface ResearchResponseBase {
  readonly schema_version: 1;
  readonly session: ResearchSessionCore;
  readonly assessment: ResearchAssessment | null;
  readonly candidates: readonly ResearchCandidate[] | null;
  readonly error_code: ResearchErrorCode | null;
  readonly acquisition_history: readonly AcquisitionHistoryAttempt[];
  readonly acquisition_history_truncated: boolean;
  readonly history?: ResearchTimeline;
}

export type ResearchResponse = ResearchResponseBase &
  (
    | { readonly required_user_action: null }
    | { readonly required_user_action: "confirm_sufficiency_or_refresh" }
    | { readonly required_user_action: "approve_candidates_or_cancel" }
  );

export interface ExportItem {
  readonly name: string;
  readonly session_id: string | null;
  readonly created_at: string | null;
  readonly manifest_valid: boolean;
  readonly export_id: string;
  readonly open_url: string | null;
}

export interface ExportsResponse {
  readonly schema_version: 1;
  readonly items: readonly ExportItem[];
  readonly limit: number;
  readonly truncated: boolean;
  readonly inventory_complete: boolean;
  readonly inventory_examined: number;
  readonly inventory_limit: number;
}

export interface SourcePreviewResult {
  readonly fingerprint: string;
  readonly source_kind: SourceKind;
  readonly selected_count: number;
  readonly video_ids: readonly string[];
  readonly videos: readonly {
    readonly video_id: string;
    readonly title: string;
    readonly published_at: string;
    readonly url: string;
  }[];
  readonly videos_returned: number;
  readonly videos_truncated: boolean;
  readonly language: string;
  readonly analyze: boolean;
  readonly requires_confirmation: boolean;
  readonly excluded_count: number;
  readonly discovery_error_count: number;
}

export interface SourceAcquisitionResult {
  readonly selected: number;
  readonly transcripts_ready: number;
  readonly insights_ready: number;
  readonly failure_count: number;
  readonly exclusion_count: number;
  readonly items: readonly {
    readonly video_id: string;
    readonly status: AcquisitionItemStatus;
    readonly error_code: AcquisitionErrorCode | null;
    readonly source_sha256: string | null;
  }[];
  readonly exit_code: number;
}

export type JobKind =
  | "source_preview"
  | "source_acquisition"
  | "research_discovery"
  | "research_acquisition"
  | "research_retry";

export type JobResultErrorCode =
  | "plan_too_large"
  | "plan_changed"
  | "stale_revision"
  | "workflow_conflict"
  | "not_found"
  | "operation_failed";

export interface JobResultError {
  readonly schema_version: 1;
  readonly error: { readonly code: JobResultErrorCode };
}

export interface TruncatedJobResult {
  readonly truncated: true;
}

export type JobSuccessResult =
  | SourcePreviewResult
  | SourceAcquisitionResult
  | ResearchResponse
  | JobResultError
  | TruncatedJobResult;

interface JobBase {
  readonly job_id: string;
  readonly kind: JobKind;
}

export type Job =
  | (JobBase & {
      readonly status: "queued" | "running";
      readonly result: null;
      readonly error_code: null;
    })
  | (JobBase & {
      readonly status: "succeeded";
      readonly result: JobSuccessResult;
      readonly error_code: null;
    })
  | (JobBase & {
      readonly status: "failed";
      readonly result: null;
      readonly error_code: "operation_failed";
    });

export interface JobResponse {
  readonly schema_version: 1;
  readonly job: Job;
}

export interface JobAcceptedResponse {
  readonly schema_version: 1;
  readonly job_id: string;
}

export interface ExportCreatedResponse {
  readonly schema_version: 1;
  readonly export: {
    readonly name: string;
    readonly manifest_sha256: string;
    readonly dossier_sha256: string;
  };
}

export interface BootstrapResponse {
  readonly schema_version: 1;
  readonly mutation_token: string;
}

export type PublicApiErrorCode =
  | "unexpected_response"
  | "invalid_request"
  | "forbidden"
  | "method_not_allowed"
  | "not_found"
  | "plan_changed"
  | "stale_revision"
  | "workflow_conflict"
  | "idempotency_conflict"
  | "request_in_progress"
  | "job_queue_full"
  | "jobs_unavailable"
  | "search_unavailable"
  | "catalog_unavailable"
  | "research_unavailable"
  | "exports_unavailable"
  | "internal_error"
  | "server_busy"
  | "server_shutting_down";

export type ApiGetResponse =
  | StatusResponse
  | SearchResponse
  | SourcesResponse
  | ResearchListResponse
  | ResearchResponse
  | JobResponse
  | ExportsResponse;

export type ApiPostResponse =
  JobAcceptedResponse | ResearchResponse | ExportCreatedResponse;

export type ApiResponse = ApiGetResponse | ApiPostResponse;

export type ApiPath = `/api/v1/${string}`;
