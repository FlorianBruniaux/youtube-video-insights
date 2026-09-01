import type { ApiGetResponse, Job, JobKind, JobResponse } from "./types";

export const RESEARCH_JOB_STORAGE_KEY = "yt-insights:research-job:v1";
const JOB_ID = /^[A-Za-z0-9_-]{1,200}$/;
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const RESEARCH_JOB_KINDS = new Set<JobKind>([
  "research_discovery",
  "research_acquisition",
  "research_retry",
]);
const MAX_POLLS = 60;

export interface ResearchJobAttempt {
  readonly version: 1;
  readonly session_id: string;
  readonly job_id: string;
  readonly kind:
    | "research_discovery"
    | "research_acquisition"
    | "research_retry";
}

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
  return (
    Object.keys(object).length === 4 &&
    object.version === 1 &&
    typeof object.session_id === "string" &&
    SESSION_ID.test(object.session_id) &&
    typeof object.job_id === "string" &&
    JOB_ID.test(object.job_id) &&
    typeof object.kind === "string" &&
    RESEARCH_JOB_KINDS.has(object.kind as JobKind)
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

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}
