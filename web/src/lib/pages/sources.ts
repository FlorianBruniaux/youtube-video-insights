import { apiGet, apiPost } from "../api";
import { createYouTubeWatchLink, replaceChildren, setText } from "../dom";
import {
  acquisitionAttemptKey,
  createBrowserAttemptIdentityCoordinator,
} from "../source-attempt-coordinator";
import type { AttemptIdentityCoordinator } from "../source-attempt-coordinator";
import type {
  ApiGetResponse,
  ApiPath,
  ApiPostResponse,
  Job,
  JobResponse,
  SourceAcquisitionResult,
  SourceItem,
  SourcePreviewResult,
  SourcesResponse,
} from "../types";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;
type WriteApi = (
  path: string,
  body: unknown,
  signal?: AbortSignal,
) => Promise<ApiPostResponse>;
type Wait = (milliseconds: number) => Promise<void>;

interface SourceDependencies {
  readonly read?: ReadApi;
  readonly write?: WriteApi;
  readonly wait?: Wait;
  readonly coordinator?: AttemptIdentityCoordinator;
}

const PAGE_SIZE = 20;
const MAX_STORED_ATTEMPT_BYTES = 1_024;
const JOB_ID = /^[A-Za-z0-9_-]{1,200}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const POLL_DELAYS: readonly number[] = [
  0,
  ...Array<number>(9).fill(500),
  ...Array<number>(20).fill(1_000),
  ...Array<number>(30).fill(2_000),
];

export const SOURCE_ATTEMPT_STORAGE_KEY = "yt-insights:source-attempt:v3";
const LEGACY_V2_SOURCE_ATTEMPT_STORAGE_KEY = "yt-insights:source-attempt:v2";
const LEGACY_V1_SOURCE_ATTEMPT_STORAGE_KEY = "yt-insights:source-attempt:v1";

type PollingSourceAttempt =
  | {
      readonly version: 3;
      readonly stage: "polling";
      readonly job_id: string;
      readonly kind: "source_preview";
      readonly fingerprint: null;
      readonly generation: null;
      readonly idempotency_key: null;
    }
  | {
      readonly version: 3;
      readonly stage: "polling";
      readonly job_id: string;
      readonly kind: "source_acquisition";
      readonly fingerprint: string;
      readonly generation: number;
      readonly idempotency_key: string;
    };

type AdmittingAcquisitionAttempt = {
  readonly version: 3;
  readonly stage: "admitting";
  readonly job_id: null;
  readonly kind: "source_acquisition";
  readonly fingerprint: string;
  readonly generation: number;
  readonly idempotency_key: string;
};

type FinalizingAcquisitionAttempt = Omit<
  AdmittingAcquisitionAttempt,
  "stage"
> & { readonly stage: "finalizing" };

type SourceAttempt =
  | PollingSourceAttempt
  | AdmittingAcquisitionAttempt
  | FinalizingAcquisitionAttempt;

export function attachSourcesPage(
  root: HTMLElement,
  dependencies: SourceDependencies = {},
): () => void {
  const read = dependencies.read ?? ((path, signal) => apiGet(path as ApiPath, signal));
  const write = dependencies.write ?? ((path, body, signal) => apiPost(path as ApiPath, body, signal));
  const wait = dependencies.wait ?? delay;
  const coordinator = dependencies.coordinator ??
    createBrowserAttemptIdentityCoordinator();
  const state = requireElement<HTMLElement>(root, "[data-source-state]");
  const list = requireElement<HTMLElement>(root, "[data-source-list]");
  const previous = requireElement<HTMLButtonElement>(root, "[data-source-prev]");
  const next = requireElement<HTMLButtonElement>(root, "[data-source-next]");
  const pageLabel = requireElement<HTMLElement>(root, "[data-source-page-label]");
  const form = requireElement<HTMLFormElement>(root, "[data-source-preview-form]");
  const previewSubmit = requireElement<HTMLButtonElement>(
    root,
    "[data-source-preview-submit]",
  );
  const planTarget = requireElement<HTMLElement>(root, "[data-source-plan]");
  const acquire = requireElement<HTMLButtonElement>(root, "[data-source-acquire]");
  const jobTarget = requireElement<HTMLElement>(root, "[data-source-job]");
  const jobIdTarget = requireElement<HTMLElement>(root, "[data-source-job-id]");
  const continueChecking = requireElement<HTMLButtonElement>(
    root,
    "[data-source-continue]",
  );
  const retryAdmission = requireElement<HTMLButtonElement>(
    root,
    "[data-source-retry-admission]",
  );
  let offset = 0;
  let inventoryController: AbortController | null = null;
  let mutationController: AbortController | null = null;
  let approvedPlan: SourcePreviewResult | null = null;
  let activeAttempt = readStoredAttempt();
  let checkingAttempt = false;

  const loadPage = async (): Promise<void> => {
    inventoryController?.abort();
    const controller = new AbortController();
    inventoryController = controller;
    previous.disabled = true;
    next.disabled = true;
    setText(state, "Loading a bounded source page…");
    try {
      const response = (await read(
        `/api/v1/sources?limit=${PAGE_SIZE}&offset=${offset}`,
        controller.signal,
      )) as SourcesResponse;
      if (controller.signal.aborted) return;
      renderInventory(list, response.items);
      previous.disabled = offset === 0;
      next.disabled = response.items.length < PAGE_SIZE;
      setText(
        pageLabel,
        response.items.length === 0
          ? offset === 0
            ? "No sources"
            : `No sources after ${offset}`
          : `Sources ${offset + 1}–${offset + response.items.length}`,
      );
      setText(state, response.items.length === 0 ? "No sources on this page." : "");
    } catch (error: unknown) {
      if (isAbort(error)) return;
      setText(state, sourceReadError(error));
      replaceChildren(list, []);
      previous.disabled = offset === 0;
      next.disabled = true;
    }
  };

  const onPrevious = (): void => {
    offset = Math.max(0, offset - PAGE_SIZE);
    void loadPage();
  };
  const onNext = (): void => {
    offset += PAGE_SIZE;
    void loadPage();
  };
  const onPreview = (event: SubmitEvent): void => {
    event.preventDefault();
    if (checkingAttempt) return;
    if (activeAttempt !== null) {
      if (activeAttempt.stage === "polling") {
        void checkAttempt(activeAttempt);
      } else if (activeAttempt.stage === "admitting") {
        showAdmissionRetry(activeAttempt);
      } else {
        showFinalizationRetry(activeAttempt);
      }
      return;
    }
    approvedPlan = null;
    planTarget.hidden = true;
    acquire.hidden = true;
    acquire.disabled = true;
    replaceChildren(planTarget, []);
    const data = new FormData(form);
    const source = String(data.get("source") ?? "").trim();
    const language = String(data.get("language") ?? "en").trim().toLowerCase();
    if (!isYouTubeSource(source)) {
      setText(jobTarget, "Enter a YouTube video, playlist, or channel URL.");
      previewSubmit.disabled = false;
      return;
    }
    mutationController?.abort();
    const controller = new AbortController();
    mutationController = controller;
    checkingAttempt = true;
    previewSubmit.disabled = true;
    continueChecking.hidden = true;
    setText(jobTarget, "Preparing a safe source preview…");
    void runPreview(source, language, data.get("analyze") === "on", controller);
  };
  const runPreview = async (
    source: string,
    language: string,
    analyze: boolean,
    controller: AbortController,
  ): Promise<void> => {
    try {
      const accepted = await write(
        "/api/v1/sources/preview",
        { source, language, analyze },
        controller.signal,
      );
      if (!("job_id" in accepted)) throw new Error("unexpected response");
      const attempt: PollingSourceAttempt = {
        version: 3,
        stage: "polling",
        job_id: accepted.job_id,
        kind: "source_preview",
        fingerprint: null,
        generation: null,
        idempotency_key: null,
      };
      activeAttempt = attempt;
      writeStoredAttempt(attempt);
      checkingAttempt = false;
      await checkAttempt(attempt, controller);
    } catch (error: unknown) {
      if (isAbort(error)) return;
      checkingAttempt = false;
      previewSubmit.disabled = false;
      setText(jobTarget, mutationError(error));
    }
  };

  const onAcquire = (): void => {
    if (approvedPlan === null || activeAttempt !== null || checkingAttempt) return;
    const plan = approvedPlan;
    checkingAttempt = true;
    acquire.disabled = true;
    previewSubmit.disabled = true;
    continueChecking.hidden = true;
    retryAdmission.hidden = true;
    setText(jobTarget, "Coordinating this acquisition attempt…");
    void prepareAcquisition(plan);
  };
  const prepareAcquisition = async (plan: SourcePreviewResult): Promise<void> => {
    try {
      const generation = await coordinator.claim(plan.fingerprint);
      const attempt: AdmittingAcquisitionAttempt = {
        version: 3,
        stage: "admitting",
        job_id: null,
        kind: "source_acquisition",
        fingerprint: plan.fingerprint,
        generation,
        idempotency_key: acquisitionAttemptKey(plan.fingerprint, generation),
      };
      activeAttempt = attempt;
      if (!writeStoredAttempt(attempt)) {
        activeAttempt = null;
        checkingAttempt = false;
        acquire.disabled = false;
        previewSubmit.disabled = false;
        setText(
          jobTarget,
          "The acquisition identity could not be saved. Allow session storage and confirm again.",
        );
        return;
      }
      approvedPlan = null;
      checkingAttempt = false;
      setText(jobTarget, "Submitting the approved acquisition…");
      await admitAcquisition(attempt);
    } catch (error: unknown) {
      checkingAttempt = false;
      activeAttempt = null;
      acquire.disabled = false;
      previewSubmit.disabled = false;
      setText(
        jobTarget,
        coordinationMessage(error),
      );
    }
  };
  const admitAcquisition = async (
    attempt: AdmittingAcquisitionAttempt,
  ): Promise<void> => {
    if (
      checkingAttempt ||
      activeAttempt?.stage !== "admitting" ||
      activeAttempt.idempotency_key !== attempt.idempotency_key
    ) {
      return;
    }
    mutationController?.abort();
    const controller = new AbortController();
    mutationController = controller;
    checkingAttempt = true;
    previewSubmit.disabled = true;
    acquire.disabled = true;
    continueChecking.hidden = true;
    retryAdmission.hidden = true;
    retryAdmission.disabled = true;
    try {
      const currentGeneration = await coordinator.claim(attempt.fingerprint);
      if (currentGeneration !== attempt.generation) {
        checkingAttempt = false;
        resetForNewPreview();
        setText(
          jobTarget,
          "This acquisition attempt is stale. Start a new preview explicitly.",
        );
        return;
      }
      const accepted = await write(
        "/api/v1/sources/acquire",
        {
          fingerprint: attempt.fingerprint,
          idempotency_key: attempt.idempotency_key,
        },
        controller.signal,
      );
      if (!("job_id" in accepted)) throw new Error("unexpected response");
      const pollingAttempt: PollingSourceAttempt = {
        version: 3,
        stage: "polling",
        job_id: accepted.job_id,
        kind: "source_acquisition",
        fingerprint: attempt.fingerprint,
        generation: attempt.generation,
        idempotency_key: attempt.idempotency_key,
      };
      activeAttempt = pollingAttempt;
      writeStoredAttempt(pollingAttempt);
      checkingAttempt = false;
      retryAdmission.disabled = false;
      await checkAttempt(pollingAttempt, controller);
    } catch (error: unknown) {
      if (isAbort(error)) return;
      checkingAttempt = false;
      retryAdmission.disabled = false;
      const code = publicCode(error);
      if (code === "plan_changed" || code === "stale_revision") {
        await finalizeRejectedAdmission(attempt);
        return;
      }
      if (code === "attempt_coordination_unavailable") {
        showAdmissionRetry(attempt, coordinationMessage(error));
        return;
      }
      showAdmissionRetry(attempt);
    }
  };

  const checkAttempt = async (
    attempt: PollingSourceAttempt,
    existingController?: AbortController,
  ): Promise<void> => {
    if (checkingAttempt || activeAttempt?.job_id !== attempt.job_id) return;
    if (mutationController !== existingController) mutationController?.abort();
    const controller = existingController ?? new AbortController();
    mutationController = controller;
    checkingAttempt = true;
    previewSubmit.disabled = true;
    acquire.disabled = true;
    continueChecking.hidden = true;
    continueChecking.disabled = true;
    retryAdmission.hidden = true;
    retryAdmission.disabled = true;
    exposeAttempt(jobIdTarget, attempt);
    setText(jobTarget, `Checking accepted ${attempt.kind === "source_preview" ? "preview" : "acquisition"} job…`);
    try {
      const completed = await pollJob(
        attempt.job_id,
        read,
        wait,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      checkingAttempt = false;
      if (attempt.kind === "source_acquisition") {
        try {
          await coordinator.complete(
            attempt.fingerprint,
            attempt.generation,
          );
        } catch (error: unknown) {
          showPollingFinalizationRetry(attempt, error);
          return;
        }
      }
      activeAttempt = null;
      clearStoredAttempt();
      previewSubmit.disabled = false;
      continueChecking.hidden = true;
      continueChecking.disabled = false;
      retryAdmission.hidden = true;
      retryAdmission.disabled = false;
      const result = requireJobSuccess(completed, attempt.kind);
      if (isJobResultError(result)) {
        setText(jobTarget, jobResultMessage(result.error.code));
        return;
      }
      if (attempt.kind === "source_preview") {
        if (!isSourcePreview(result)) {
          setText(jobTarget, "The preview response could not be used safely.");
          return;
        }
        approvedPlan = result;
        renderPlan(planTarget, result);
        planTarget.hidden = false;
        acquire.hidden = false;
        acquire.disabled = false;
        setText(jobTarget, "Preview ready. Review every selected video ID before acquiring.");
        return;
      }
      if (!isAcquisitionResult(result)) {
        setText(jobTarget, "The acquisition result could not be used safely.");
        return;
      }
      setText(
        jobTarget,
        `${result.transcripts_ready} transcripts ready from ${result.selected} selected videos.`,
      );
      void loadPage();
    } catch (error: unknown) {
      if (isAbort(error)) return;
      checkingAttempt = false;
      if (publicCode(error) === "not_found") {
        if (attempt.kind === "source_acquisition") {
          try {
            await coordinator.complete(
              attempt.fingerprint,
              attempt.generation,
            );
          } catch (coordinationFailure: unknown) {
            showPollingFinalizationRetry(attempt, coordinationFailure);
            return;
          }
        }
        resetForNewPreview();
        setText(
          jobTarget,
          "This accepted job is no longer available. Start a new preview explicitly.",
        );
        return;
      }
      previewSubmit.disabled = false;
      continueChecking.hidden = activeAttempt === null;
      continueChecking.disabled = false;
      retryAdmission.hidden = true;
      retryAdmission.disabled = false;
      setText(jobTarget, mutationError(error));
    }
  };

  const onContinue = (): void => {
    if (activeAttempt?.stage === "polling" && !checkingAttempt) {
      void checkAttempt(activeAttempt);
    }
  };

  const onRetryAdmission = (): void => {
    if (activeAttempt?.stage === "admitting" && !checkingAttempt) {
      void admitAcquisition(activeAttempt);
    } else if (activeAttempt?.stage === "finalizing" && !checkingAttempt) {
      void finalizeRejectedAdmission(activeAttempt);
    }
  };

  const showAdmissionRetry = (
    attempt: AdmittingAcquisitionAttempt,
    message = "The acquisition response was not received. Retry admission with the same saved identity.",
  ): void => {
    activeAttempt = attempt;
    previewSubmit.disabled = true;
    acquire.disabled = true;
    continueChecking.hidden = true;
    retryAdmission.hidden = false;
    retryAdmission.disabled = false;
    jobIdTarget.textContent = "";
    retryAdmission.textContent = "Retry admission";
    setText(jobTarget, message);
  };

  const finalizeRejectedAdmission = async (
    attempt: AdmittingAcquisitionAttempt | FinalizingAcquisitionAttempt,
  ): Promise<void> => {
    const finalizing: FinalizingAcquisitionAttempt = {
      ...attempt,
      stage: "finalizing",
    };
    activeAttempt = finalizing;
    writeStoredAttempt(finalizing);
    checkingAttempt = true;
    retryAdmission.disabled = true;
    try {
      await coordinator.complete(finalizing.fingerprint, finalizing.generation);
      checkingAttempt = false;
      resetForNewPreview();
      setText(
        jobTarget,
        "The source plan changed. Preview the source again before acquiring.",
      );
    } catch (error: unknown) {
      checkingAttempt = false;
      showFinalizationRetry(finalizing, error);
    }
  };

  const showFinalizationRetry = (
    attempt: FinalizingAcquisitionAttempt,
    error?: unknown,
  ): void => {
    activeAttempt = attempt;
    previewSubmit.disabled = true;
    acquire.disabled = true;
    continueChecking.hidden = true;
    retryAdmission.hidden = false;
    retryAdmission.disabled = false;
    retryAdmission.textContent = "Retry finalization";
    jobIdTarget.textContent = "";
    setText(jobTarget, coordinationMessage(error));
  };

  const showPollingFinalizationRetry = (
    attempt: PollingSourceAttempt & { readonly kind: "source_acquisition" },
    error: unknown,
  ): void => {
    activeAttempt = attempt;
    previewSubmit.disabled = true;
    acquire.disabled = true;
    continueChecking.hidden = false;
    continueChecking.disabled = false;
    retryAdmission.hidden = true;
    retryAdmission.disabled = false;
    setText(
      jobTarget,
      `${coordinationMessage(error)} Use Continue checking to finalize this exact job without resubmitting it.`,
    );
  };

  const resetForNewPreview = (): void => {
    activeAttempt = null;
    approvedPlan = null;
    clearStoredAttempt();
    previewSubmit.disabled = false;
    acquire.disabled = true;
    acquire.hidden = true;
    planTarget.hidden = true;
    replaceChildren(planTarget, []);
    continueChecking.hidden = true;
    continueChecking.disabled = false;
    retryAdmission.hidden = true;
    retryAdmission.disabled = false;
    retryAdmission.textContent = "Retry admission";
    jobIdTarget.textContent = "";
  };

  previous.addEventListener("click", onPrevious);
  next.addEventListener("click", onNext);
  form.addEventListener("submit", onPreview);
  acquire.addEventListener("click", onAcquire);
  continueChecking.addEventListener("click", onContinue);
  retryAdmission.addEventListener("click", onRetryAdmission);
  void loadPage();
  if (activeAttempt !== null) {
    if (activeAttempt.stage === "polling") {
      exposeAttempt(jobIdTarget, activeAttempt);
      void checkAttempt(activeAttempt);
    } else if (activeAttempt.stage === "admitting") {
      showAdmissionRetry(activeAttempt);
    } else {
      showFinalizationRetry(activeAttempt);
    }
  }

  return () => {
    inventoryController?.abort();
    mutationController?.abort();
    previous.removeEventListener("click", onPrevious);
    next.removeEventListener("click", onNext);
    form.removeEventListener("submit", onPreview);
    acquire.removeEventListener("click", onAcquire);
    continueChecking.removeEventListener("click", onContinue);
    retryAdmission.removeEventListener("click", onRetryAdmission);
  };
}

export async function pollJob(
  jobId: string,
  read: ReadApi,
  wait: Wait,
  signal?: AbortSignal,
): Promise<JobResponse> {
  for (const pause of POLL_DELAYS) {
    if (signal?.aborted) throw new DOMException("Operation aborted", "AbortError");
    if (pause > 0) await wait(pause);
    const response = (await read(
      `/api/v1/jobs/${encodeURIComponent(jobId)}`,
      signal,
    )) as JobResponse;
    if (response.job.job_id !== jobId) throw { code: "job_mismatch" };
    if (response.job.status === "succeeded" || response.job.status === "failed") {
      return response;
    }
  }
  throw { code: "poll_timeout" };
}

function renderInventory(target: HTMLElement, items: readonly SourceItem[]): void {
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "No sources found on this page.";
    replaceChildren(target, [empty]);
    return;
  }
  const table = document.createElement("table");
  table.className = "source-table";
  const caption = document.createElement("caption");
  caption.textContent = "Videos in the local corpus";
  const head = document.createElement("thead");
  const header = document.createElement("tr");
  for (const label of [
    "Video",
    "Published",
    "Source labels",
    "Language",
    "Transcript",
    "Index",
  ]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    header.append(cell);
  }
  head.append(header);
  const body = document.createElement("tbody");
  for (const item of items) {
    const row = document.createElement("tr");
    row.append(
      tableVideoCell(item),
      textCell(formatPublishedAt(item.published_at)),
      textCell(item.sources.join(", ") || "Unknown"),
      textCell(item.languages.join(", ") || "Unknown"),
      textCell(item.transcript_state),
      textCell(item.index_state.replaceAll("_", " ")),
    );
    body.append(row);
  }
  table.append(caption, head, body);

  const cards = document.createElement("div");
  cards.className = "source-cards";
  cards.dataset.sourceCards = "";
  for (const item of items) cards.append(sourceCard(item));
  replaceChildren(target, [table, cards]);
}

function tableVideoCell(item: SourceItem): HTMLTableCellElement {
  const cell = document.createElement("td");
  const link = createYouTubeWatchLink(item.title, item.url);
  if (link) cell.append(link);
  const id = document.createElement("code");
  id.textContent = item.video_id;
  cell.append(id);
  return cell;
}

function textCell(value: string): HTMLTableCellElement {
  const cell = document.createElement("td");
  cell.textContent = value;
  return cell;
}

function sourceCard(item: SourceItem): HTMLElement {
  const article = document.createElement("article");
  article.className = "source-card";
  const title = document.createElement("h3");
  const link = createYouTubeWatchLink(item.title, item.url);
  if (link) title.append(link);
  const id = labelledValue("Video ID", item.video_id);
  const published = labelledValue("Published", formatPublishedAt(item.published_at));
  const sources = labelledValue("Sources", item.sources.join(", ") || "Unknown");
  const language = labelledValue("Language", item.languages.join(", ") || "Unknown");
  const transcript = labelledValue("Transcript", item.transcript_state);
  const index = labelledValue("Index", item.index_state.replaceAll("_", " "));
  replaceChildren(article, [title, id, published, sources, language, transcript, index]);
  return article;
}

function labelledValue(label: string, value: string): HTMLElement {
  const row = document.createElement("p");
  const name = document.createElement("strong");
  name.textContent = `${label}: `;
  row.append(name, document.createTextNode(value));
  return row;
}

function renderPlan(target: HTMLElement, plan: SourcePreviewResult): void {
  const heading = document.createElement("h3");
  heading.id = "source-plan-title";
  heading.textContent = `Review ${plan.selected_count} selected video${plan.selected_count === 1 ? "" : "s"}`;
  const summary = document.createElement("p");
  summary.textContent = `${plan.source_kind} · language ${plan.language} · ${plan.excluded_count} excluded · ${plan.discovery_error_count} discovery errors`;
  const instruction = document.createElement("p");
  instruction.textContent = "Confirm that every exact ID below belongs in this acquisition.";
  const list = document.createElement("ol");
  list.className = "selected-video-list";
  for (const videoId of plan.video_ids) {
    const row = document.createElement("li");
    const details = plan.videos.find((video) => video.video_id === videoId);
    const code = document.createElement("code");
    code.textContent = videoId;
    row.append(code);
    if (details) row.append(document.createTextNode(` · ${details.title}`));
    list.append(row);
  }
  replaceChildren(target, [heading, summary, instruction, list]);
}

function requireJobSuccess(response: JobResponse, kind: Job["kind"]): NonNullable<Job["result"]> {
  if (response.job.kind !== kind || response.job.status !== "succeeded") {
    throw { code: response.job.status === "failed" ? "operation_failed" : "unexpected_response" };
  }
  return response.job.result;
}

function isJobResultError(value: NonNullable<Job["result"]>): value is Extract<NonNullable<Job["result"]>, { error: unknown }> {
  return "error" in value;
}

function isSourcePreview(value: NonNullable<Job["result"]>): value is SourcePreviewResult {
  return "fingerprint" in value && "video_ids" in value;
}

function isAcquisitionResult(value: NonNullable<Job["result"]>): value is SourceAcquisitionResult {
  return "selected" in value && "transcripts_ready" in value;
}

function jobResultMessage(code: string): string {
  if (code === "plan_too_large") return "This source plan is too large. Narrow the source and preview again.";
  if (code === "stale_revision" || code === "plan_changed") return "The source plan changed. Preview it again before acquiring.";
  if (code === "workflow_conflict") return "This operation conflicts with the current workflow state.";
  return "The job failed. Review the source and start a new explicit attempt if needed.";
}

function mutationError(error: unknown): string {
  const code = publicCode(error);
  if (code === "attempt_coordination_unavailable") return coordinationMessage(error);
  if (code === "forbidden") return "The server denied mutation permission. Reload the page and preview again.";
  if (code === "plan_changed" || code === "stale_revision") return "The source plan changed. Preview it again before acquiring.";
  if (code === "job_queue_full" || code === "server_busy") return "The local job queue is busy. Start a new explicit attempt later.";
  if (code === "poll_timeout") return "The job is still running. Use Continue checking to resume this exact job.";
  if (code === "job_mismatch") return "The accepted job could not be verified. Continue checking the original job before starting another attempt.";
  return "Cannot complete this operation. Check that the local server is running.";
}

function coordinationMessage(error: unknown): string {
  return publicCode(error) === "attempt_coordination_unavailable"
    ? "Shared acquisition coordination storage is unavailable or corrupt. No acquisition was submitted. Repair browser storage, then retry the explicit action."
    : "The acquisition identity could not be finalized safely. No new acquisition will be admitted.";
}

function sourceReadError(error: unknown): string {
  return publicCode(error) === "catalog_unavailable"
    ? "Source inventory is unavailable. The catalog may need repair."
    : "Cannot reach the local source inventory.";
}

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isYouTubeSource(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      (url.hostname === "youtube.com" || url.hostname === "www.youtube.com" || url.hostname === "youtu.be") &&
      url.username === "" && url.password === "" && url.port === "";
  } catch {
    return false;
  }
}

function formatPublishedAt(value: string | null): string {
  return value === null ? "Unknown" : value.slice(0, 10);
}

function exposeAttempt(target: HTMLElement, attempt: SourceAttempt): void {
  target.textContent = attempt.job_id ?? "";
}

function readStoredAttempt(): SourceAttempt | null {
  const current = readStorageValue(SOURCE_ATTEMPT_STORAGE_KEY);
  if (current !== null) return parseCurrentAttempt(current);
  const legacyV2 = migrateStoredAttempt(
    LEGACY_V2_SOURCE_ATTEMPT_STORAGE_KEY,
    parseLegacyV2Attempt,
  );
  if (legacyV2 !== null) return legacyV2;
  return migrateStoredAttempt(
    LEGACY_V1_SOURCE_ATTEMPT_STORAGE_KEY,
    parseLegacyV1Attempt,
  );
}

function migrateStoredAttempt(
  key: string,
  parse: (raw: string) => SourceAttempt | null,
): SourceAttempt | null {
  const raw = readStorageValue(key);
  if (raw === null) return null;
  const migrated = parse(raw);
  discardInvalidStoredAttempt(key);
  if (migrated !== null) writeStoredAttempt(migrated);
  return migrated;
}

function readStorageValue(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function parseCurrentAttempt(raw: string): SourceAttempt | null {
  if (raw.length > MAX_STORED_ATTEMPT_BYTES) {
    discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
    return null;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
    return null;
  }
  if (!isRecord(value)) {
    discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
    return null;
  }
  const keys = Object.keys(value).sort();
  const expected = [
    "fingerprint",
    "generation",
    "idempotency_key",
    "job_id",
    "kind",
    "stage",
    "version",
  ];
  if (
    keys.length !== expected.length ||
    keys.some((key, index) => key !== expected[index]) ||
    value.version !== 3
  ) {
    discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
    return null;
  }
  if (
    value.stage === "polling" &&
    value.kind === "source_preview" &&
    typeof value.job_id === "string" &&
    JOB_ID.test(value.job_id) &&
    value.fingerprint === null &&
    value.generation === null &&
    value.idempotency_key === null
  ) {
    return {
      version: 3,
      stage: "polling",
      job_id: value.job_id,
      kind: "source_preview",
      fingerprint: null,
      generation: null,
      idempotency_key: null,
    };
  }
  if (
    value.kind === "source_acquisition" &&
    typeof value.fingerprint === "string" &&
    SHA256.test(value.fingerprint) &&
    isSafeGeneration(value.generation) &&
    typeof value.idempotency_key === "string" &&
    isSafeIdempotencyKey(value.idempotency_key)
  ) {
    if (
      (value.stage === "admitting" || value.stage === "finalizing") &&
      value.job_id === null &&
      isExpectedAttemptKey(
        value.fingerprint,
        value.generation,
        value.idempotency_key,
      )
    ) {
      return {
        version: 3,
        stage: value.stage,
        job_id: null,
        kind: "source_acquisition",
        fingerprint: value.fingerprint,
        generation: value.generation,
        idempotency_key: value.idempotency_key,
      };
    }
    if (
      value.stage !== "polling" ||
      typeof value.job_id !== "string" ||
      !JOB_ID.test(value.job_id)
    ) {
      discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
      return null;
    }
    return {
      version: 3,
      stage: "polling",
      job_id: value.job_id,
      kind: "source_acquisition",
      fingerprint: value.fingerprint,
      generation: value.generation,
      idempotency_key: value.idempotency_key,
    };
  }
  discardInvalidStoredAttempt(SOURCE_ATTEMPT_STORAGE_KEY);
  return null;
}

function parseLegacyV2Attempt(raw: string): SourceAttempt | null {
  const value = parseLegacyRecord(raw, 2, true);
  if (value === null) return null;
  if (
    value.kind === "source_preview" &&
    value.stage === "polling" &&
    typeof value.job_id === "string" &&
    value.fingerprint === null &&
    value.idempotency_key === null
  ) {
    return previewAttempt(value.job_id);
  }
  if (
    value.kind !== "source_acquisition" ||
    typeof value.fingerprint !== "string" ||
    !SHA256.test(value.fingerprint) ||
    typeof value.idempotency_key !== "string" ||
    !isSafeIdempotencyKey(value.idempotency_key)
  ) {
    return null;
  }
  if (value.stage === "admitting" && value.job_id === null) {
    return {
      version: 3,
      stage: "admitting",
      job_id: null,
      kind: "source_acquisition",
      fingerprint: value.fingerprint,
      generation: 0,
      idempotency_key: value.idempotency_key,
    };
  }
  if (value.stage !== "polling" || typeof value.job_id !== "string") return null;
  return acquisitionPollingAttempt(
    value.job_id,
    value.fingerprint,
    value.idempotency_key,
  );
}

function parseLegacyV1Attempt(raw: string): PollingSourceAttempt | null {
  const value = parseLegacyRecord(raw, 1, false);
  if (value === null || typeof value.job_id !== "string") return null;
  if (
    value.kind === "source_preview" &&
    value.fingerprint === null &&
    value.idempotency_key === null
  ) {
    return previewAttempt(value.job_id);
  }
  if (
    value.kind === "source_acquisition" &&
    typeof value.fingerprint === "string" &&
    SHA256.test(value.fingerprint) &&
    typeof value.idempotency_key === "string" &&
    isSafeIdempotencyKey(value.idempotency_key)
  ) {
    return acquisitionPollingAttempt(
      value.job_id,
      value.fingerprint,
      value.idempotency_key,
    );
  }
  return null;
}

function parseLegacyRecord(
  raw: string,
  version: 1 | 2,
  hasStage: boolean,
): Record<string, unknown> | null {
  if (raw.length > MAX_STORED_ATTEMPT_BYTES) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  const expected = [
    "fingerprint",
    "idempotency_key",
    "job_id",
    "kind",
    ...(hasStage ? ["stage"] : []),
    "version",
  ].sort();
  if (
    keys.length !== expected.length ||
    keys.some((key, index) => key !== expected[index]) ||
    value.version !== version ||
    (value.job_id !== null &&
      (typeof value.job_id !== "string" || !JOB_ID.test(value.job_id)))
  ) {
    return null;
  }
  return value;
}

function previewAttempt(jobId: string): PollingSourceAttempt {
  return {
    version: 3,
    stage: "polling",
    job_id: jobId,
    kind: "source_preview",
    fingerprint: null,
    generation: null,
    idempotency_key: null,
  };
}

function acquisitionPollingAttempt(
  jobId: string,
  fingerprint: string,
  idempotencyKey: string,
): PollingSourceAttempt {
  return {
    version: 3,
    stage: "polling",
    job_id: jobId,
    kind: "source_acquisition",
    fingerprint,
    generation: 0,
    idempotency_key: idempotencyKey,
  };
}

function writeStoredAttempt(attempt: SourceAttempt): boolean {
  try {
    window.sessionStorage.setItem(
      SOURCE_ATTEMPT_STORAGE_KEY,
      JSON.stringify(attempt),
    );
    return true;
  } catch {
    return false;
  }
}

function clearStoredAttempt(): void {
  try {
    window.sessionStorage.removeItem(SOURCE_ATTEMPT_STORAGE_KEY);
    window.sessionStorage.removeItem(LEGACY_V2_SOURCE_ATTEMPT_STORAGE_KEY);
    window.sessionStorage.removeItem(LEGACY_V1_SOURCE_ATTEMPT_STORAGE_KEY);
  } catch {
    // Terminal state remains authoritative even when browser storage is unavailable.
  }
}

function discardInvalidStoredAttempt(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Invalid browser state is ignored when storage cannot be changed.
  }
}

function isSafeIdempotencyKey(value: string): boolean {
  if (value.length === 0 || value.length > 200) return false;
  return [...value].every((character) => {
    const code = character.codePointAt(0);
    return code !== undefined && code >= 0x20 && code <= 0x7e;
  });
}

function isSafeGeneration(value: unknown): value is number {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 999_999_999;
}

function isExpectedAttemptKey(
  fingerprint: string,
  generation: number,
  key: string,
): boolean {
  return key === acquisitionAttemptKey(fingerprint, generation) ||
    (generation === 0 && key === `web-source-acquire-${fingerprint}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
