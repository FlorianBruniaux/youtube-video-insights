import { apiGet, apiPost } from "../api";
import { createYouTubeWatchLink, replaceChildren, setText } from "../dom";
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
}

const PAGE_SIZE = 20;
const POLL_DELAYS = [0, 150, 250, 400, 650, 1_000, 1_500, 2_000] as const;

export function attachSourcesPage(
  root: HTMLElement,
  dependencies: SourceDependencies = {},
): () => void {
  const read = dependencies.read ?? ((path, signal) => apiGet(path as ApiPath, signal));
  const write = dependencies.write ?? ((path, body, signal) => apiPost(path as ApiPath, body, signal));
  const wait = dependencies.wait ?? delay;
  const state = requireElement<HTMLElement>(root, "[data-source-state]");
  const list = requireElement<HTMLElement>(root, "[data-source-list]");
  const previous = requireElement<HTMLButtonElement>(root, "[data-source-prev]");
  const next = requireElement<HTMLButtonElement>(root, "[data-source-next]");
  const pageLabel = requireElement<HTMLElement>(root, "[data-source-page-label]");
  const form = requireElement<HTMLFormElement>(root, "[data-source-preview-form]");
  const planTarget = requireElement<HTMLElement>(root, "[data-source-plan]");
  const acquire = requireElement<HTMLButtonElement>(root, "[data-source-acquire]");
  const jobTarget = requireElement<HTMLElement>(root, "[data-source-job]");
  let offset = 0;
  let inventoryController: AbortController | null = null;
  let mutationController: AbortController | null = null;
  let approvedPlan: SourcePreviewResult | null = null;

  const loadPage = async (): Promise<void> => {
    inventoryController?.abort();
    const controller = new AbortController();
    inventoryController = controller;
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
    mutationController?.abort();
    const controller = new AbortController();
    mutationController = controller;
    approvedPlan = null;
    planTarget.hidden = true;
    acquire.hidden = true;
    acquire.disabled = true;
    replaceChildren(planTarget, []);
    setText(jobTarget, "Preparing a safe source preview…");
    const data = new FormData(form);
    const source = String(data.get("source") ?? "").trim();
    const language = String(data.get("language") ?? "en").trim().toLowerCase();
    if (!isYouTubeSource(source)) {
      setText(jobTarget, "Enter a YouTube video, playlist, or channel URL.");
      return;
    }
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
      const completed = await pollJob(accepted.job_id, read, wait, controller.signal);
      if (controller.signal.aborted) return;
      const result = requireJobSuccess(completed, "source_preview");
      if (isJobResultError(result)) {
        setText(jobTarget, jobResultMessage(result.error.code));
        return;
      }
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
    } catch (error: unknown) {
      if (isAbort(error)) return;
      setText(jobTarget, mutationError(error));
    }
  };

  const onAcquire = (): void => {
    if (approvedPlan === null) return;
    const plan = approvedPlan;
    acquire.disabled = true;
    mutationController?.abort();
    const controller = new AbortController();
    mutationController = controller;
    setText(jobTarget, "Acquiring the approved videos…");
    void runAcquire(plan, controller);
  };
  const runAcquire = async (
    plan: SourcePreviewResult,
    controller: AbortController,
  ): Promise<void> => {
    try {
      const accepted = await write(
        "/api/v1/sources/acquire",
        { fingerprint: plan.fingerprint, idempotency_key: idempotencyKey() },
        controller.signal,
      );
      if (!("job_id" in accepted)) throw new Error("unexpected response");
      const completed = await pollJob(accepted.job_id, read, wait, controller.signal);
      if (controller.signal.aborted) return;
      const result = requireJobSuccess(completed, "source_acquisition");
      if (isJobResultError(result)) {
        setText(jobTarget, jobResultMessage(result.error.code));
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
      setText(jobTarget, mutationError(error));
    }
  };

  previous.addEventListener("click", onPrevious);
  next.addEventListener("click", onNext);
  form.addEventListener("submit", onPreview);
  acquire.addEventListener("click", onAcquire);
  void loadPage();

  return () => {
    inventoryController?.abort();
    mutationController?.abort();
    previous.removeEventListener("click", onPrevious);
    next.removeEventListener("click", onNext);
    form.removeEventListener("submit", onPreview);
    acquire.removeEventListener("click", onAcquire);
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
  for (const label of ["Video", "Language", "Transcript", "Index"]) {
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
  const language = labelledValue("Language", item.languages.join(", ") || "Unknown");
  const transcript = labelledValue("Transcript", item.transcript_state);
  const index = labelledValue("Index", item.index_state.replaceAll("_", " "));
  replaceChildren(article, [title, id, language, transcript, index]);
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
  if (code === "forbidden") return "The server denied mutation permission. Reload the page and preview again.";
  if (code === "plan_changed" || code === "stale_revision") return "The source plan changed. Preview it again before acquiring.";
  if (code === "job_queue_full" || code === "server_busy") return "The local job queue is busy. Start a new explicit attempt later.";
  if (code === "poll_timeout") return "The job is still running. Reload the page to check it before starting another attempt.";
  return "Cannot complete this operation. Check that the local server is running.";
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

function idempotencyKey(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `web-source-${random}`;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
