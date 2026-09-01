import { apiGet, apiPost } from "../api";
import { createYouTubeWatchLink, replaceChildren } from "../dom";
import {
  clearResearchAdmission,
  clearResearchJobAttempt,
  createResearchAdmission,
  pollResearchJob,
  readResearchAdmission,
  readResearchJobAttempt,
  researchActionScope,
  researchAdmissionBody,
  researchScopeFingerprint,
  writeResearchAdmission,
  writeResearchJobAttempt,
} from "../research-job";
import type { ResearchAdmissionAttempt, ResearchJobAttempt } from "../research-job";
import { createBrowserResearchAttemptIdentityCoordinator } from "../source-attempt-coordinator";
import type { AttemptIdentityCoordinator } from "../source-attempt-coordinator";
import type {
  ApiGetResponse,
  ApiPath,
  ApiPostResponse,
  Job,
  JobAcceptedResponse,
  JobResultError,
  ResearchCandidate,
  ResearchResponse,
} from "../types";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;
type WriteApi = (path: string, body: unknown, signal?: AbortSignal) => Promise<ApiPostResponse>;
type Wait = (milliseconds: number) => Promise<void>;

interface WorkspaceDependencies {
  readonly read?: ReadApi;
  readonly write?: WriteApi;
  readonly wait?: Wait;
  readonly createId?: () => string;
  readonly coordinator?: AttemptIdentityCoordinator;
}

const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const JOB_ID = /^[A-Za-z0-9_-]{1,200}$/;
const LANGUAGE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const ADMISSION_TIMEOUT_MS = 15_000;
const STALE_MESSAGE = "The session changed. Review the current evidence before deciding again.";

export function attachResearchWorkspace(
  root: HTMLElement,
  dependencies: WorkspaceDependencies = {},
): () => void {
  const read = dependencies.read ?? ((path, signal) => apiGet(path as ApiPath, signal));
  const write = dependencies.write ?? ((path, body, signal) => apiPost(path as ApiPath, body, signal));
  const wait = dependencies.wait ?? delay;
  const createId = dependencies.createId ?? (() => crypto.randomUUID());
  const coordinator = dependencies.coordinator ??
    createBrowserResearchAttemptIdentityCoordinator();
  const status = requireElement<HTMLElement>(root, "[data-research-status]");
  const heading = requireElement<HTMLElement>(root, "[data-research-heading]");
  const evidence = requireElement<HTMLElement>(root, "[data-evidence-panel]");
  const decision = requireElement<HTMLElement>(root, "[data-decision-panel]");
  const candidates = requireElement<HTMLElement>(root, "[data-candidate-list]");
  const acquisitionHistory = requireElement<HTMLElement>(root, "[data-acquisition-history]");
  const jobRegion = requireElement<HTMLElement>(root, "[data-job-progress]");
  const jobMessage = requireElement<HTMLElement>(root, "[data-job-message]");
  const jobId = requireElement<HTMLElement>(root, "[data-job-id]");
  const continueJob = requireElement<HTMLButtonElement>(root, "[data-job-continue]");
  const retryAdmission = requireElement<HTMLButtonElement>(root, "[data-job-retry-admission]");
  const eventList = requireElement<HTMLElement>(root, "[data-event-list]");
  const sessionId = sessionIdFromPath(window.location.pathname);
  let snapshot: ResearchResponse | null = null;
  let requestController: AbortController | null = null;
  let jobController: AbortController | null = null;
  let activeJob = readResearchJobAttempt();
  let activeAdmission = readResearchAdmission();
  let busy = false;
  let disposed = false;

  if (sessionId === null) {
    status.textContent = "This research workspace URL is invalid.";
    return () => undefined;
  }
  if (activeJob !== null && activeJob.session_id !== sessionId) activeJob = null;
  if (activeAdmission !== null && activeAdmission.session_id !== sessionId) activeAdmission = null;
  if (activeJob !== null && activeAdmission !== null) {
    clearResearchAdmission();
    activeAdmission = null;
  }

  const loadSnapshot = async (): Promise<ResearchResponse | null> => {
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    try {
      const response = await read(`/api/v1/research/sessions/${sessionId}`, controller.signal);
      if (controller.signal.aborted || disposed || !("session" in response)) return null;
      const research = response as ResearchResponse;
      if (research.session.session_id !== sessionId) throw new Error("Session identity mismatch");
      snapshot = research;
      renderSnapshot(research);
      return research;
    } catch (error: unknown) {
      if (isAbort(error)) return null;
      status.textContent = publicCode(error) === "not_found"
        ? "This research session was not found."
        : "The research snapshot is unavailable.";
      return null;
    }
  };

  const renderSnapshot = (research: ResearchResponse): void => {
    renderHeading(heading, research);
    renderEvidence(evidence, research);
    renderCandidates(candidates, research.candidates);
    renderAcquisitionHistory(acquisitionHistory, research);
    renderTimeline(eventList, research);
    renderDecision(decision, research);
  };

  const handleStale = async (): Promise<void> => {
    await loadSnapshot();
    status.textContent = STALE_MESSAGE;
  };

  const syncMutation = async (path: string, body: unknown): Promise<void> => {
    if (busy) return;
    busy = true;
    setDecisionButtonsDisabled(decision, true);
    const controller = new AbortController();
    requestController?.abort();
    requestController = controller;
    status.textContent = "Saving your explicit decision…";
    try {
      const response = await write(path, body, controller.signal);
      if (!("session" in response)) throw new Error("Unexpected research response");
      snapshot = response as ResearchResponse;
      renderSnapshot(snapshot);
      status.textContent = "Decision saved.";
    } catch (error: unknown) {
      if (isAbort(error)) return;
      if (publicCode(error) === "stale_revision") {
        await handleStale();
      } else {
        status.textContent = mutationMessage(error);
        setDecisionButtonsDisabled(decision, false);
      }
    } finally {
      busy = false;
    }
  };

  const submitAdmission = async (
    kind: ResearchAdmissionAttempt["kind"],
    expectedRevision: number,
    language: string | null,
    retryAttempt: ResearchAdmissionAttempt | null,
  ): Promise<void> => {
    if (
      busy ||
      activeJob !== null ||
      (retryAttempt !== null &&
        activeAdmission?.idempotency_key !== retryAttempt.idempotency_key)
    ) return;
    busy = true;
    setDecisionButtonsDisabled(decision, true);
    retryAdmission.hidden = true;
    retryAdmission.disabled = true;
    status.textContent = "Submitting one replay-safe background job…";
    const controller = new AbortController();
    requestController?.abort();
    requestController = controller;
    let attempt = retryAttempt;
    try {
      const scope = researchActionScope(
        sessionId,
        kind,
        expectedRevision,
        language,
      );
      const scopeFingerprint = await researchScopeFingerprint(scope);
      if (
        retryAttempt?.version === 2 &&
        retryAttempt.scope_fingerprint !== scopeFingerprint
      ) throw { code: "invalid_request", status: 400 };
      const admission = await coordinator.admit(
        scopeFingerprint,
        retryAttempt === null
          ? null
          : retryAttempt.version === 2
            ? retryAttempt.generation
            : 0,
        async (generation) => {
          const coordinatedAttempt = retryAttempt ?? createResearchAdmission(
            sessionId,
            kind,
            expectedRevision,
            language,
            scopeFingerprint,
            generation,
          );
          attempt = coordinatedAttempt;
          activeAdmission = coordinatedAttempt;
          if (!writeResearchAdmission(coordinatedAttempt)) {
            throw { code: "attempt_storage_unavailable" };
          }
          return awaitAdmissionResponse(write, coordinatedAttempt, controller.signal);
        },
      );
      if (admission.status === "stale") {
        clearResearchAdmission();
        activeAdmission = null;
        await loadSnapshot();
        status.textContent = "This research attempt already ended. Review the current session before starting again.";
        busy = false;
        setDecisionButtonsDisabled(decision, false);
        return;
      }
      const response = admission.value;
      if (!("job_id" in response) || !JOB_ID.test((response as JobAcceptedResponse).job_id)) {
        throw new Error("Invalid accepted job");
      }
      if (attempt === null) throw new Error("Missing research admission");
      const accepted = response as JobAcceptedResponse;
      const jobAttempt = acceptedJobAttempt(attempt, accepted.job_id);
      if (writeResearchJobAttempt(jobAttempt)) {
        clearResearchAdmission();
        activeAdmission = null;
      } else {
        status.textContent = "The job was accepted, but its identity could not be stored. Keep this page open and continue checking this exact job.";
      }
      activeJob = jobAttempt;
      busy = false;
      await checkJob(jobAttempt, false);
    } catch (error: unknown) {
      if (isAbort(error)) {
        busy = false;
        return;
      }
      const code = publicCode(error);
      if (code === "stale_revision") {
        clearResearchAdmission();
        activeAdmission = null;
        await handleStale();
      } else if (code === "workflow_conflict") {
        clearResearchAdmission();
        activeAdmission = null;
        await loadSnapshot();
        status.textContent = "The research workflow changed. Review the current session before starting again.";
      } else if (code === "not_found") {
        clearResearchAdmission();
        activeAdmission = null;
        status.textContent = "This research session was not found.";
      } else if (code === "attempt_storage_unavailable") {
        clearResearchAdmission();
        activeAdmission = null;
        status.textContent = "Browser session storage is unavailable. No background job was submitted.";
      } else if (attempt !== null && shouldRetainAdmission(error)) {
        showAdmission(jobRegion, jobMessage, jobId, retryAdmission, attempt);
        status.textContent = "The admission response was not confirmed. Retry explicitly to reuse the same request identity.";
      } else {
        clearResearchAdmission();
        activeAdmission = null;
        status.textContent = mutationMessage(error);
      }
      busy = false;
      setDecisionButtonsDisabled(decision, false);
    }
  };

  const startJob = (
    kind: ResearchAdmissionAttempt["kind"],
    language: string | null = null,
  ): void => {
    if (busy || activeJob !== null || activeAdmission !== null || snapshot === null) return;
    void submitAdmission(
      kind,
      snapshot.session.revision,
      language,
      null,
    );
  };

  const checkJob = async (attempt: ResearchJobAttempt, immediate: boolean): Promise<void> => {
    if (disposed || busy || activeJob?.job_id !== attempt.job_id) return;
    busy = true;
    jobController?.abort();
    const controller = new AbortController();
    jobController = controller;
    showJob(jobRegion, jobMessage, jobId, continueJob, attempt, "Checking the background job…", false);
    try {
      if (attempt.version === 2) {
        const expectedScope = researchActionScope(
          attempt.session_id,
          attempt.kind,
          attempt.expected_revision,
          attempt.language,
        );
        const expectedFingerprint = await researchScopeFingerprint(expectedScope);
        if (expectedFingerprint !== attempt.scope_fingerprint) {
          clearResearchJobAttempt();
          activeJob = null;
          await loadSnapshot();
          status.textContent = "The stored background job identity was invalid. The durable session was reloaded.";
          return;
        }
      }
      const result = await pollResearchJob(attempt, read, wait, controller.signal, { immediate });
      if (disposed) return;
      if (result.status === "paused") {
        const message = result.reason === "hidden"
          ? "Checking paused while this page is hidden."
          : result.reason === "limit"
            ? "The bounded polling window ended. Continue checking this exact job."
            : "Checking paused.";
        showJob(jobRegion, jobMessage, jobId, continueJob, attempt, message, true);
        return;
      }
      if (result.status === "missing") {
        clearResearchJobAttempt();
        activeJob = null;
        showJob(jobRegion, jobMessage, jobId, continueJob, attempt, "The background job is no longer retained. Reloading the durable session…", false);
        const loaded = await loadSnapshot();
        if (loaded !== null) clearAdmissionForJob(attempt);
        status.textContent = "The job record expired. The durable session was reloaded.";
        return;
      }
      const terminalMessage = terminalJobMessage(result.job);
      showJob(jobRegion, jobMessage, jobId, continueJob, attempt, terminalMessage, false);
      if (attempt.version === 2) {
        try {
          await coordinator.complete(
            attempt.scope_fingerprint,
            attempt.generation,
          );
        } catch {
          showJob(
            jobRegion,
            jobMessage,
            jobId,
            continueJob,
            attempt,
            "The job ended, but its attempt identity could not be finalized. Continue checking this exact job.",
            true,
          );
          return;
        }
      }
      clearResearchJobAttempt();
      activeJob = null;
      const loaded = await loadSnapshot();
      if (loaded !== null) clearAdmissionForJob(attempt);
      status.textContent = terminalMessage;
    } catch (error: unknown) {
      if (isAbort(error)) return;
      showJob(jobRegion, jobMessage, jobId, continueJob, attempt, "Cannot check this job now. Continue checking the same job explicitly.", true);
    } finally {
      busy = false;
    }
  };

  const onDecisionClick = (event: Event): void => {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>("button");
    if (button == null || snapshot === null || busy) return;
    const revision = snapshot.session.revision;
    const path = `/api/v1/research/sessions/${sessionId}`;
    if (button.dataset.decision === "sufficient" || button.dataset.decision === "refresh") {
      void syncMutation(`${path}/decisions`, {
        expected_revision: revision,
        decision: button.dataset.decision,
        idempotency_key: createId(),
      });
      return;
    }
    if (button.dataset.startDiscovery !== undefined) {
      startJob("research_discovery");
      return;
    }
    if (button.dataset.approveCandidates !== undefined) {
      const checked = [...candidates.querySelectorAll<HTMLInputElement>("[data-candidate-id]:checked")]
        .map((input) => input.value);
      if (checked.length < 1 || checked.length > 5) {
        status.textContent = "Select 1 to 5 exact candidate videos.";
        return;
      }
      void syncMutation(`${path}/approvals`, {
        expected_revision: revision,
        video_ids: checked,
        idempotency_key: createId(),
      });
      return;
    }
    if (button.dataset.startAcquisition !== undefined) {
      const language = decision.querySelector<HTMLInputElement>("[data-acquisition-language]")?.value.trim().toLowerCase() ?? "";
      if (!LANGUAGE.test(language) || codePoints(language) > 500) {
        status.textContent = "Enter a valid transcript language, such as en or fr.";
        return;
      }
      startJob("research_acquisition", language);
      return;
    }
    if (button.dataset.retryResearch !== undefined) {
      startJob("research_retry");
      return;
    }
    if (button.dataset.cancelResearch !== undefined) {
      if (!window.confirm("Cancel this research session? This decision is durable.")) return;
      void syncMutation(`${path}/cancellations`, {
        expected_revision: revision,
        idempotency_key: createId(),
      });
      return;
    }
    if (button.dataset.createExport !== undefined) {
      void createExport(`${path}/exports`);
    }
  };

  const createExport = async (path: string): Promise<void> => {
    if (busy) return;
    busy = true;
    setDecisionButtonsDisabled(decision, true);
    status.textContent = "Creating the deterministic export…";
    try {
      const response = await write(path, { force: false });
      if (!("export" in response)) throw new Error("Unexpected export response");
      status.textContent = `Export created: ${response.export.name}`;
    } catch (error: unknown) {
      if (!isAbort(error)) status.textContent = mutationMessage(error);
    } finally {
      busy = false;
      setDecisionButtonsDisabled(decision, false);
    }
  };

  const onCandidateChange = (): void => updateCandidateSelection(candidates, decision);
  const clearAdmissionForJob = (attempt: ResearchJobAttempt): void => {
    if (
      activeAdmission?.session_id === attempt.session_id &&
      activeAdmission.kind === attempt.kind
    ) {
      clearResearchAdmission();
      activeAdmission = null;
    }
  };
  const onContinue = (): void => {
    if (activeJob !== null && !busy) void checkJob(activeJob, true);
  };
  const onRetryAdmission = (): void => {
    if (activeAdmission !== null && !busy) {
      void submitAdmission(
        activeAdmission.kind,
        activeAdmission.expected_revision,
        activeAdmission.language,
        activeAdmission,
      );
    }
  };
  const onVisibility = (): void => {
    if (document.hidden) {
      jobController?.abort();
      return;
    }
    if (activeJob !== null && !busy) void checkJob(activeJob, true);
  };
  decision.addEventListener("click", onDecisionClick);
  candidates.addEventListener("change", onCandidateChange);
  continueJob.addEventListener("click", onContinue);
  retryAdmission.addEventListener("click", onRetryAdmission);
  document.addEventListener("visibilitychange", onVisibility);

  status.textContent = "Loading the durable research snapshot…";
  if (activeJob !== null) {
    void checkJob(activeJob, true);
  } else {
    void loadSnapshot().then((loaded) => {
      if (loaded !== null && status.textContent === "Loading the durable research snapshot…") status.textContent = "Research snapshot loaded.";
      if (loaded !== null && activeAdmission !== null) {
        showAdmission(jobRegion, jobMessage, jobId, retryAdmission, activeAdmission);
        status.textContent = "A background job admission needs an explicit same-key retry.";
      }
    });
  }

  return () => {
    disposed = true;
    requestController?.abort();
    jobController?.abort();
    decision.removeEventListener("click", onDecisionClick);
    candidates.removeEventListener("change", onCandidateChange);
    continueJob.removeEventListener("click", onContinue);
    retryAdmission.removeEventListener("click", onRetryAdmission);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

function renderHeading(target: HTMLElement, response: ResearchResponse): void {
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = `Research · ${humanize(response.session.state)}`;
  const title = document.createElement("h1");
  title.textContent = response.session.topic;
  const meta = document.createElement("p");
  meta.textContent = `Revision ${response.session.revision} · Updated ${formatTimestamp(response.session.updated_at)} · ${response.session.freshness_profile} freshness`;
  replaceChildren(target, [eyebrow, title, meta]);
}

function renderEvidence(target: HTMLElement, response: ResearchResponse): void {
  const title = document.createElement("h2");
  title.textContent = "Evidence and coverage";
  const assessment = response.assessment;
  if (assessment === null) {
    const empty = document.createElement("p");
    empty.textContent = "No local assessment is available yet.";
    replaceChildren(target, [title, empty]);
    return;
  }
  const metrics = document.createElement("div");
  metrics.className = "coverage-grid";
  const metricValues: readonly [string, string][] = [
    ["Matched passages", String(assessment.coverage.matched_passages)],
    ["Matched videos", String(assessment.coverage.matched_videos)],
    ["Distinct channels", String(assessment.coverage.distinct_channels)],
    ["Newest source", assessment.coverage.newest_source_published_at ?? "Unknown"],
  ];
  for (const [label, value] of metricValues) {
    const card = document.createElement("article");
    const heading = document.createElement("h3");
    heading.textContent = label;
    const metric = document.createElement("p");
    metric.textContent = value;
    card.append(heading, metric);
    metrics.append(card);
  }
  const freshness = document.createElement("p");
  freshness.className = assessment.freshness.stale ? "freshness-warning" : "freshness-current";
  freshness.textContent = `${assessment.freshness.stale ? "Evidence may be stale" : "Evidence is current"}: ${assessment.freshness.reason}`;
  const zero = document.createElement("p");
  zero.textContent = assessment.coverage.queries_with_zero_hits.length === 0
    ? "Every query has local evidence."
    : `Queries without local evidence: ${assessment.coverage.queries_with_zero_hits.join(", ")}`;
  const passages = document.createElement("div");
  passages.className = "evidence-list";
  for (const passage of assessment.passages) {
    const card = document.createElement("article");
    card.className = "evidence-card";
    const cardTitle = document.createElement("h3");
    cardTitle.textContent = passage.query;
    const excerpt = document.createElement("p");
    excerpt.textContent = passage.excerpt;
    const meta = document.createElement("p");
    meta.className = "evidence-meta";
    meta.textContent = `Video ${passage.video_id} · Channel ${passage.channel_id} · Rank ${passage.rank}`;
    card.append(cardTitle, excerpt, meta);
    const matchingVideo = assessment.videos.find((video) => video.video_id === passage.video_id);
    if (matchingVideo !== undefined) {
      const link = createYouTubeWatchLink("Open video", matchingVideo.watch_url);
      if (link !== null) card.append(link);
    }
    passages.append(card);
  }
  replaceChildren(target, [title, metrics, freshness, zero, passages]);
}

function renderDecision(target: HTMLElement, response: ResearchResponse): void {
  const children: Node[] = [];
  const title = document.createElement("h2");
  children.push(title);
  if (response.required_user_action === "confirm_sufficiency_or_refresh") {
    title.textContent = "Is the current evidence sufficient?";
    const text = document.createElement("p");
    text.textContent = "Choose one explicit next step. Refreshing only authorizes metadata discovery.";
    const actions = document.createElement("div");
    actions.className = "decision-actions";
    actions.append(
      actionButton("Use current evidence", "decision", "sufficient", true),
      actionButton("Search YouTube for more", "decision", "refresh", true),
    );
    children.push(text, actions);
  } else if (response.required_user_action === "approve_candidates_or_cancel") {
    title.textContent = "Approve exact videos";
    const text = document.createElement("p");
    text.textContent = "Select 1 to 5 candidate IDs. Acquisition remains a separate explicit step.";
    const button = actionButton("Approve selected candidates", "approveCandidates", "", false);
    button.dataset.approveCandidates = "";
    button.disabled = true;
    const cancel = actionButton("Cancel research", "cancelResearch", "", false);
    cancel.dataset.cancelResearch = "";
    cancel.classList.add("button-danger-secondary");
    children.push(text, button, cancel);
  } else if (response.session.state === "discovering") {
    title.textContent = "Candidate discovery is ready";
    const text = document.createElement("p");
    text.textContent = "Start one metadata-only YouTube discovery job. It will not acquire transcripts.";
    const button = actionButton("Start candidate discovery", "startDiscovery", "", false);
    button.dataset.startDiscovery = "";
    children.push(text, button);
  } else if (response.session.state === "acquiring") {
    title.textContent = "Approved candidates are ready";
    const text = document.createElement("p");
    text.textContent = "Start one acquisition job for the exact approved IDs.";
    const label = document.createElement("label");
    label.textContent = "Transcript language";
    const language = document.createElement("input");
    language.dataset.acquisitionLanguage = "";
    language.value = response.session.languages[0] ?? "en";
    language.maxLength = 500;
    label.append(language);
    const button = actionButton("Acquire approved videos", "startAcquisition", "", false);
    button.dataset.startAcquisition = "";
    children.push(text, label, button);
  } else if (response.session.state === "failed_retryable") {
    title.textContent = "This step failed";
    const text = document.createElement("p");
    text.textContent = `${humanize(response.error_code ?? "research_unavailable")}. Nothing will retry automatically.`;
    const button = actionButton("Retry this failed step", "retryResearch", "", false);
    button.dataset.retryResearch = "";
    children.push(text, button);
  } else {
    title.textContent = response.session.state === "completed" ? "Research complete" : "No decision required";
    const text = document.createElement("p");
    text.textContent = response.session.state === "completed"
      ? "The durable evidence is ready for a deterministic export."
      : `Current state: ${humanize(response.session.state)}.`;
    children.push(text);
  }
  if (isExportable(response.session.state)) {
    const exportButton = actionButton("Create dossier export", "createExport", "", false);
    exportButton.classList.add("button-secondary");
    exportButton.dataset.createExport = "";
    children.push(exportButton);
  }
  replaceChildren(target, children);
}

function renderCandidates(target: HTMLElement, items: readonly ResearchCandidate[] | null): void {
  const title = document.createElement("h2");
  title.textContent = "Discovery candidates";
  if (items === null || items.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "No candidate snapshot is available.";
    replaceChildren(target, [title, empty]);
    return;
  }
  const list = document.createElement("div");
  list.className = "candidate-grid";
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "candidate-card";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = item.video_id;
    checkbox.dataset.candidateId = "";
    checkbox.disabled = item.status !== "candidate";
    if (checkbox.disabled) checkbox.dataset.originallyDisabled = "true";
    const name = document.createElement("span");
    name.textContent = item.title || item.video_id;
    label.append(checkbox, name);
    const meta = document.createElement("p");
    meta.textContent = `${item.video_id} · ${item.channel_title ?? "Unknown channel"} · ${item.published_at ?? "Unknown date"} · ${humanize(item.status)}`;
    const queries = document.createElement("p");
    queries.textContent = `Matched: ${item.matched_queries.join(", ") || "No query label"}`;
    const link = createYouTubeWatchLink("Review video", item.watch_url);
    card.append(label, meta, queries);
    if (link !== null) card.append(link);
    list.append(card);
  }
  replaceChildren(target, [title, list]);
}

function renderAcquisitionHistory(target: HTMLElement, response: ResearchResponse): void {
  const title = document.createElement("h2");
  title.textContent = "Acquisition history";
  if (response.acquisition_history.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "No acquisition attempts yet.";
    replaceChildren(target, [title, empty]);
    return;
  }
  const list = document.createElement("ol");
  for (const attempt of response.acquisition_history) {
    const item = document.createElement("li");
    const summary = document.createElement("strong");
    summary.textContent = `${attempt.attempt_id}: ${humanize(attempt.status)}`;
    const outcomes = document.createElement("ul");
    for (const outcome of attempt.items) {
      const line = document.createElement("li");
      line.textContent = `${outcome.video_id}: ${humanize(outcome.status)}${outcome.error_code === null ? "" : ` (${humanize(outcome.error_code)})`}`;
      outcomes.append(line);
    }
    item.append(summary, outcomes);
    list.append(item);
  }
  const children: Node[] = [title, list];
  if (response.acquisition_history_truncated) {
    const warning = document.createElement("p");
    warning.textContent = "Only the latest bounded acquisition attempts are shown.";
    children.push(warning);
  }
  replaceChildren(target, children);
}

function renderTimeline(target: HTMLElement, response: ResearchResponse): void {
  const history = response.history;
  if (history === undefined) {
    target.textContent = "No event timeline is available.";
    return;
  }
  const list = document.createElement("ol");
  for (const event of history.events) {
    const item = document.createElement("li");
    item.textContent = `${formatTimestamp(event.created_at)}: ${humanize(event.event_code)} (${humanize(event.from_state ?? "start")} → ${humanize(event.to_state)})`;
    list.append(item);
  }
  for (const savedDecision of history.decisions) {
    const item = document.createElement("li");
    item.textContent = `${formatTimestamp(savedDecision.created_at)}: decision ${humanize(savedDecision.action)}`;
    list.append(item);
  }
  if (history.events_truncated || history.decisions_truncated) {
    const item = document.createElement("li");
    item.textContent = "Earlier timeline entries are outside this bounded view.";
    list.append(item);
  }
  replaceChildren(target, [list]);
}

function updateCandidateSelection(candidates: HTMLElement, decision: HTMLElement): void {
  const boxes = [...candidates.querySelectorAll<HTMLInputElement>("[data-candidate-id]")];
  const checked = boxes.filter((box) => box.checked);
  for (const box of boxes) {
    if (!box.checked && checked.length >= 5) box.disabled = true;
    else if (box.dataset.originallyDisabled !== "true") box.disabled = false;
  }
  const approve = decision.querySelector<HTMLButtonElement>("[data-approve-candidates]");
  if (approve !== null) approve.disabled = checked.length < 1 || checked.length > 5;
}

function showJob(
  region: HTMLElement,
  message: HTMLElement,
  id: HTMLElement,
  continuation: HTMLButtonElement,
  attempt: ResearchJobAttempt,
  text: string,
  canContinue: boolean,
): void {
  region.hidden = false;
  message.textContent = text;
  id.textContent = attempt.job_id;
  continuation.hidden = !canContinue;
  continuation.disabled = !canContinue;
}

function showAdmission(
  region: HTMLElement,
  message: HTMLElement,
  id: HTMLElement,
  retry: HTMLButtonElement,
  attempt: ResearchAdmissionAttempt,
): void {
  region.hidden = false;
  message.textContent = "This background job admission has no confirmed response.";
  id.textContent = attempt.idempotency_key;
  retry.hidden = false;
  retry.disabled = false;
}

function acceptedJobAttempt(
  admission: ResearchAdmissionAttempt,
  jobId: string,
): ResearchJobAttempt {
  if (admission.version === 1) {
    return {
      version: 1,
      session_id: admission.session_id,
      job_id: jobId,
      kind: admission.kind,
    };
  }
  return {
    version: 2,
    session_id: admission.session_id,
    job_id: jobId,
    kind: admission.kind,
    expected_revision: admission.expected_revision,
    language: admission.language,
    scope_fingerprint: admission.scope_fingerprint,
    generation: admission.generation,
    idempotency_key: admission.idempotency_key,
  };
}

async function awaitAdmissionResponse(
  write: WriteApi,
  attempt: ResearchAdmissionAttempt,
  lifecycleSignal: AbortSignal,
): Promise<ApiPostResponse> {
  const requestController = new AbortController();
  let timedOut = false;
  const aborted = new Promise<never>((_resolve, reject) => {
    requestController.signal.addEventListener("abort", () => {
      reject(
        timedOut
          ? { code: "admission_timeout" }
          : new DOMException("Operation aborted", "AbortError"),
      );
    }, { once: true });
  });
  const onLifecycleAbort = (): void => requestController.abort();
  lifecycleSignal.addEventListener("abort", onLifecycleAbort, { once: true });
  if (lifecycleSignal.aborted) onLifecycleAbort();
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    requestController.abort();
  }, ADMISSION_TIMEOUT_MS);
  try {
    if (requestController.signal.aborted) return await aborted;
    return await Promise.race([
      write(
        researchAdmissionPath(attempt),
        researchAdmissionBody(attempt),
        requestController.signal,
      ),
      aborted,
    ]);
  } finally {
    globalThis.clearTimeout(timeout);
    lifecycleSignal.removeEventListener("abort", onLifecycleAbort);
  }
}

function researchAdmissionPath(attempt: ResearchAdmissionAttempt): string {
  const action = {
    research_discovery: "discovery",
    research_acquisition: "acquisition",
    research_retry: "retry",
  }[attempt.kind];
  return `/api/v1/research/sessions/${attempt.session_id}/${action}`;
}

function terminalJobMessage(job: Job): string {
  if (job.status === "failed") return "The background job failed. No retry was started.";
  if (job.status !== "succeeded" || job.result === null) return "The background job ended.";
  if ("error" in job.result) {
    const code = (job.result as JobResultError).error.code;
    if (code === "stale_revision") return STALE_MESSAGE;
    return `The background job ended with ${humanize(code)}. No retry was started.`;
  }
  if ("truncated" in job.result) return "The job result was truncated. The durable session was reloaded.";
  return "The background job completed. The durable session was reloaded.";
}

function actionButton(
  label: string,
  dataName: string,
  dataValue: string,
  primary: boolean,
): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = primary ? "button" : "button button-secondary";
  button.textContent = label;
  if (dataName === "decision") button.dataset.decision = dataValue;
  else button.dataset[dataName] = dataValue;
  if (primary) button.dataset.primaryChoice = "";
  return button;
}

function setDecisionButtonsDisabled(target: HTMLElement, disabled: boolean): void {
  for (const button of target.querySelectorAll<HTMLButtonElement>("button")) button.disabled = disabled;
}

function sessionIdFromPath(path: string): string | null {
  const match = path.match(/^\/research\/([A-Za-z0-9_-]{1,128})\/?$/);
  const value = match?.[1] ?? null;
  return value !== null && SESSION_ID.test(value) ? value : null;
}

function mutationMessage(error: unknown): string {
  const code = publicCode(error);
  if (code === "workflow_conflict") return "This action is no longer available in the current research state. Reload the session.";
  if (code === "idempotency_conflict") return "This request identity conflicts with another decision. Reload before trying again.";
  if (code === "job_queue_full" || code === "server_busy") return "The local work queue is busy. Start a new explicit attempt later.";
  return "The local server could not complete this explicit action.";
}

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

function shouldRetainAdmission(error: unknown): boolean {
  const code = publicCode(error);
  if (
    code === "request_in_progress" ||
    code === "admission_timeout" ||
    code === "unexpected_response"
  ) return true;
  const status = publicStatus(error);
  if (status === 429 || status === 503 || (status !== null && status >= 500)) return true;
  return code === null;
}

function publicStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) return null;
  return typeof error.status === "number" ? error.status : null;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function codePoints(value: string): number {
  return [...value].length;
}

function formatTimestamp(value: string): string {
  return `${value.slice(0, 16).replace("T", " ")} UTC`;
}

function isExportable(state: ResearchResponse["session"]["state"]): boolean {
  return state === "awaiting_sufficiency_confirmation" || state === "awaiting_candidate_approval" || state === "completed";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
