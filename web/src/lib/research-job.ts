import type { ApiGetResponse, Job, JobKind, JobResponse } from "./types";
import { researchAttemptKey } from "./source-attempt-coordinator";

export const RESEARCH_JOB_STORAGE_KEY = "yt-insights:research-job:v1";
export const RESEARCH_ADMISSION_STORAGE_KEY = "yt-insights:research-admission:v1";
const JOB_ID = /^[A-Za-z0-9_-]{1,200}$/;
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const RESEARCH_JOB_KINDS = new Set<JobKind>([
  "research_discovery",
  "research_acquisition",
  "research_retry",
]);
const MAX_POLLS = 60;
const LANGUAGE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

interface LegacyResearchJobAttempt {
  readonly version: 1;
  readonly session_id: string;
  readonly job_id: string;
  readonly kind:
    | "research_discovery"
    | "research_acquisition"
    | "research_retry";
}

interface CoordinatedResearchJobAttempt {
  readonly version: 2;
  readonly session_id: string;
  readonly job_id: string;
  readonly kind: ResearchJobKind;
  readonly expected_revision: number;
  readonly language: string | null;
  readonly scope_fingerprint: string;
  readonly generation: number;
  readonly idempotency_key: string;
}

export type ResearchJobAttempt =
  | LegacyResearchJobAttempt
  | CoordinatedResearchJobAttempt;

interface LegacyResearchAdmissionAttempt {
  readonly version: 1;
  readonly session_id: string;
  readonly kind: ResearchJobKind;
  readonly expected_revision: number;
  readonly idempotency_key: string;
  readonly language: string | null;
}

interface CoordinatedResearchAdmissionAttempt {
  readonly version: 2;
  readonly session_id: string;
  readonly kind: ResearchJobKind;
  readonly expected_revision: number;
  readonly idempotency_key: string;
  readonly language: string | null;
  readonly scope_fingerprint: string;
  readonly generation: number;
}

export type ResearchAdmissionAttempt =
  | LegacyResearchAdmissionAttempt
  | CoordinatedResearchAdmissionAttempt;

type ResearchJobKind =
  | "research_discovery"
  | "research_acquisition"
  | "research_retry";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;
type Wait = (milliseconds: number) => Promise<void>;

export type ResearchPollResult =
  | { readonly status: "terminal"; readonly job: Job }
  | { readonly status: "missing" }
  | { readonly status: "paused"; readonly reason: "hidden" | "aborted" | "limit" };

export async function pollResearchJob(
  attempt: ResearchJobAttempt,
  read: ReadApi,
  wait: Wait,
  signal: AbortSignal,
  options: { readonly immediate?: boolean } = {},
): Promise<ResearchPollResult> {
  if (!isResearchJobAttempt(attempt)) throw new TypeError("Invalid research job attempt");
  for (let index = 0; index < MAX_POLLS; index += 1) {
    if (signal.aborted) return { status: "paused", reason: "aborted" };
    if (document.hidden) return { status: "paused", reason: "hidden" };
    if (!(options.immediate === true && index === 0)) {
      await waitUntilReady(
        Promise.resolve(wait(index === 0 ? 500 : index === 1 ? 1_000 : 2_000)),
        signal,
      );
    }
    if (signal.aborted) return { status: "paused", reason: "aborted" };
    if (document.hidden) return { status: "paused", reason: "hidden" };
    let response: ApiGetResponse;
    try {
      response = await read(`/api/v1/jobs/${attempt.job_id}`, signal);
    } catch (error: unknown) {
      if (publicCode(error) === "not_found") return { status: "missing" };
      throw error;
    }
    if (!("job" in response)) throw new Error("Unexpected job response");
    const job = (response as JobResponse).job;
    if (job.job_id !== attempt.job_id || job.kind !== attempt.kind) {
      throw new Error("Research job identity mismatch");
    }
    if (job.status === "succeeded" || job.status === "failed") {
      return { status: "terminal", job };
    }
  }
  return { status: "paused", reason: "limit" };
}

async function waitUntilReady(waiting: Promise<void>, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return;
  await new Promise<void>((resolve, reject) => {
    const finish = (error?: unknown): void => {
      signal.removeEventListener("abort", onAbort);
      if (error === undefined) resolve();
      else reject(error);
    };
    const onAbort = (): void => finish();
    signal.addEventListener("abort", onAbort, { once: true });
    waiting.then(() => finish(), (error: unknown) => finish(error));
  });
}

export function isResearchJobAttempt(value: unknown): value is ResearchJobAttempt {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const object = value as Record<string, unknown>;
  const common =
    typeof object.session_id === "string" &&
    SESSION_ID.test(object.session_id) &&
    typeof object.job_id === "string" &&
    JOB_ID.test(object.job_id) &&
    typeof object.kind === "string" &&
    RESEARCH_JOB_KINDS.has(object.kind as JobKind);
  if (!common) return false;
  if (object.version === 1) return (
    Object.keys(object).length === 4 &&
    object.version === 1
  );
  return (
    object.version === 2 &&
    Object.keys(object).length === 9 &&
    isRevision(object.expected_revision) &&
    isResearchLanguage(object.kind as ResearchJobKind, object.language) &&
    typeof object.scope_fingerprint === "string" &&
    SHA256.test(object.scope_fingerprint) &&
    isGeneration(object.generation) &&
    typeof object.idempotency_key === "string" &&
    object.idempotency_key === coordinatedKey(
      object.scope_fingerprint,
      object.generation,
    )
  );
}

export function readResearchJobAttempt(): ResearchJobAttempt | null {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(RESEARCH_JOB_STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null || raw.length > 1_024) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isResearchJobAttempt(parsed)) return parsed;
  } catch {
    // The corrupt value is discarded below.
  }
  clearResearchJobAttempt();
  return null;
}

export function writeResearchJobAttempt(attempt: ResearchJobAttempt): boolean {
  if (!isResearchJobAttempt(attempt)) return false;
  try {
    window.sessionStorage.setItem(RESEARCH_JOB_STORAGE_KEY, JSON.stringify(attempt));
    return true;
  } catch {
    return false;
  }
}

export function clearResearchJobAttempt(): void {
  try {
    window.sessionStorage.removeItem(RESEARCH_JOB_STORAGE_KEY);
  } catch {
    // The in-memory controller still stops polling this attempt.
  }
}

export function createResearchAdmission(
  sessionId: string,
  kind: ResearchAdmissionAttempt["kind"],
  expectedRevision: number,
  language: string | null,
  scopeFingerprint: string,
  generation: number,
): ResearchAdmissionAttempt {
  if (
    !SESSION_ID.test(sessionId) ||
    !RESEARCH_JOB_KINDS.has(kind) ||
    !isRevision(expectedRevision) ||
    !isResearchLanguage(kind, language) ||
    !SHA256.test(scopeFingerprint) ||
    !isGeneration(generation)
  ) {
    throw new TypeError("Invalid research admission identity");
  }
  return {
    version: 2,
    session_id: sessionId,
    kind,
    expected_revision: expectedRevision,
    idempotency_key: coordinatedKey(scopeFingerprint, generation),
    language,
    scope_fingerprint: scopeFingerprint,
    generation,
  };
}

export function researchActionScope(
  sessionId: string,
  kind: ResearchJobKind,
  expectedRevision: number,
  language: string | null,
): string {
  if (
    !SESSION_ID.test(sessionId) ||
    !RESEARCH_JOB_KINDS.has(kind) ||
    !isRevision(expectedRevision) ||
    !isResearchLanguage(kind, language)
  ) throw new TypeError("Invalid research action scope");
  return JSON.stringify([sessionId, kind, expectedRevision, language]);
}

export async function researchScopeFingerprint(scope: string): Promise<string> {
  if (scope.length < 1 || [...scope].length > 800) {
    throw new TypeError("Invalid research action scope");
  }
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) throw { code: "attempt_coordination_unavailable" };
  try {
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(scope));
    return [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    throw { code: "attempt_coordination_unavailable" };
  }
}

export function researchAdmissionBody(
  attempt: ResearchAdmissionAttempt,
): Record<string, string | number> {
  if (!isResearchAdmissionAttempt(attempt)) throw new TypeError("Invalid research admission");
  return {
    expected_revision: attempt.expected_revision,
    idempotency_key: attempt.idempotency_key,
    ...(attempt.language === null ? {} : { language: attempt.language }),
  };
}

export function readResearchAdmission(): ResearchAdmissionAttempt | null {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(RESEARCH_ADMISSION_STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null || raw.length > 1_500) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isResearchAdmissionAttempt(parsed)) return parsed;
  } catch {
    // The corrupt value is discarded below.
  }
  clearResearchAdmission();
  return null;
}

export function writeResearchAdmission(attempt: ResearchAdmissionAttempt): boolean {
  if (!isResearchAdmissionAttempt(attempt)) return false;
  try {
    window.sessionStorage.setItem(
      RESEARCH_ADMISSION_STORAGE_KEY,
      JSON.stringify(attempt),
    );
    return true;
  } catch {
    return false;
  }
}

export function clearResearchAdmission(): void {
  try {
    window.sessionStorage.removeItem(RESEARCH_ADMISSION_STORAGE_KEY);
  } catch {
    // The controller still clears its in-memory admission.
  }
}

function isResearchAdmissionAttempt(value: unknown): value is ResearchAdmissionAttempt {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const object = value as Record<string, unknown>;
  if (
    typeof object.session_id !== "string" ||
    !SESSION_ID.test(object.session_id) ||
    typeof object.kind !== "string" ||
    !RESEARCH_JOB_KINDS.has(object.kind as JobKind) ||
    !isRevision(object.expected_revision) ||
    typeof object.idempotency_key !== "string" ||
    object.idempotency_key.length > 200 ||
    !/^[\x20-\x7e]+$/.test(object.idempotency_key) ||
    !isResearchLanguage(object.kind as ResearchJobKind, object.language)
  ) return false;
  if (object.version === 1) {
    if (Object.keys(object).length !== 6) return false;
    return object.idempotency_key === legacyKey(
      object.session_id,
      object.kind as ResearchJobKind,
      object.expected_revision,
      object.language as string | null,
    );
  }
  return (
    object.version === 2 &&
    Object.keys(object).length === 8 &&
    typeof object.scope_fingerprint === "string" &&
    SHA256.test(object.scope_fingerprint) &&
    isGeneration(object.generation) &&
    object.idempotency_key === coordinatedKey(
      object.scope_fingerprint,
      object.generation,
    )
  );
}

function coordinatedKey(scopeFingerprint: string, generation: number): string {
  return researchAttemptKey(scopeFingerprint, generation);
}

function legacyKey(
  sessionId: string,
  kind: ResearchJobKind,
  expectedRevision: number,
  language: string | null,
): string {
  const languageSuffix = language === null ? "" : `:${hashText(language)}`;
  return `web:${kind}:${sessionId}:${expectedRevision}${languageSuffix}`;
}

function hashText(value: string): string {
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(value)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return hash.toString(16).padStart(16, "0");
}

function isRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isGeneration(value: unknown): value is number {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 999_999_999;
}

function isResearchLanguage(kind: ResearchJobKind, value: unknown): boolean {
  if (kind !== "research_acquisition") return value === null;
  return typeof value === "string" &&
    LANGUAGE.test(value) &&
    [...value].length <= 500;
}

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}
