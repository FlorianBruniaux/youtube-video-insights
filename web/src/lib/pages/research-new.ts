import { apiPost } from "../api";
import type { ApiPath, ApiPostResponse, FreshnessProfile, ResearchResponse } from "../types";

const START_STORAGE_KEY = "yt-insights:research-start:v1";
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const LANGUAGE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const PROFILES = new Set<FreshnessProfile>(["fast", "standard", "stable", "historical"]);

type WriteApi = (path: string, body: unknown, signal?: AbortSignal) => Promise<ApiPostResponse>;

interface StartPayload {
  readonly topic: string;
  readonly queries: readonly string[];
  readonly languages: readonly string[];
  readonly freshness_profile: FreshnessProfile;
  readonly idempotency_key: string;
}

interface StoredStart {
  readonly version: 1;
  readonly payload: StartPayload;
}

interface ResearchNewDependencies {
  readonly write?: WriteApi;
  readonly navigate?: (path: string) => void;
  readonly createId?: () => string;
}

export function attachResearchNewPage(
  root: HTMLElement,
  dependencies: ResearchNewDependencies = {},
): () => void {
  const write = dependencies.write ?? ((path, body, signal) => apiPost(path as ApiPath, body, signal));
  const navigate = dependencies.navigate ?? ((path) => window.location.assign(path));
  const createId = dependencies.createId ?? (() => crypto.randomUUID());
  const form = requireElement<HTMLFormElement>(root, "[data-research-new-form]");
  const submit = requireElement<HTMLButtonElement>(root, "[data-research-new-submit]");
  const status = requireElement<HTMLElement>(root, "[data-research-new-status]");
  const retry = requireElement<HTMLButtonElement>(root, "[data-research-new-retry]");
  let controller: AbortController | null = null;
  let busy = false;

  const send = async (payload: StartPayload): Promise<void> => {
    if (busy) return;
    busy = true;
    submit.disabled = true;
    retry.disabled = true;
    retry.hidden = true;
    status.textContent = "Creating the durable research session…";
    controller?.abort();
    controller = new AbortController();
    try {
      const response = await write("/api/v1/research/sessions", payload, controller.signal);
      if (!("session" in response)) throw new Error("Unexpected research response");
      const research = response as ResearchResponse;
      if (!SESSION_ID.test(research.session.session_id)) throw new Error("Invalid session identifier");
      clearStoredStart();
      navigate(`/research/${encodeURIComponent(research.session.session_id)}/`);
    } catch (error: unknown) {
      if (isAbort(error)) return;
      status.textContent = startError(error);
      retry.hidden = false;
      retry.disabled = false;
    } finally {
      busy = false;
      submit.disabled = false;
    }
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (busy) return;
    let payload: StartPayload;
    try {
      payload = parseStartForm(form, createId());
    } catch (error: unknown) {
      status.textContent = error instanceof Error ? error.message : "Review the research fields.";
      return;
    }
    if (!storeStart({ version: 1, payload })) {
      status.textContent = "Browser session storage is unavailable. No research was submitted.";
      return;
    }
    void send(payload);
  };
  const onRetry = (): void => {
    if (busy) return;
    const stored = readStoredStart();
    if (stored === null) {
      retry.hidden = true;
      status.textContent = "The saved request is unavailable. Review the form and submit a new request.";
      return;
    }
    void send(stored.payload);
  };
  form.addEventListener("submit", onSubmit);
  retry.addEventListener("click", onRetry);
  return () => {
    controller?.abort();
    form.removeEventListener("submit", onSubmit);
    retry.removeEventListener("click", onRetry);
  };
}

function parseStartForm(form: HTMLFormElement, idempotencyKey: string): StartPayload {
  const data = new FormData(form);
  const topic = String(data.get("topic") ?? "").trim();
  if (topic.length < 1 || codePoints(topic) > 500) throw new Error("Enter a topic of at most 500 characters.");
  const queries = unique(
    String(data.get("queries") ?? "")
      .split(/\r?\n/u)
      .map((value) => value.trim())
      .filter(Boolean),
  );
  if (queries.length < 1 || queries.length > 8 || queries.some((value) => codePoints(value) > 500)) {
    throw new Error("Enter 1 to 8 distinct queries, one per line.");
  }
  const languages = unique(
    String(data.get("languages") ?? "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean),
  );
  if (languages.length > 20 || languages.some((value) => !LANGUAGE.test(value))) {
    throw new Error("Use up to 20 comma-separated language tags, such as en or fr.");
  }
  const profile = String(data.get("freshness_profile") ?? "");
  if (!PROFILES.has(profile as FreshnessProfile)) throw new Error("Choose a valid freshness profile.");
  if (typeof idempotencyKey !== "string" || idempotencyKey.length < 8 || idempotencyKey.length > 200) {
    throw new Error("Cannot create a safe request identity.");
  }
  return {
    topic,
    queries,
    languages,
    freshness_profile: profile as FreshnessProfile,
    idempotency_key: idempotencyKey,
  };
}

function storeStart(value: StoredStart): boolean {
  try {
    const serialized = JSON.stringify(value);
    if (serialized.length > 12_000) return false;
    window.sessionStorage.setItem(START_STORAGE_KEY, serialized);
    return true;
  } catch {
    return false;
  }
}

function readStoredStart(): StoredStart | null {
  try {
    const raw = window.sessionStorage.getItem(START_STORAGE_KEY);
    if (raw === null || raw.length > 12_000) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
    const stored = value as Partial<StoredStart>;
    if (
      Object.keys(value).length !== 2 ||
      stored.version !== 1 ||
      !isStartPayload(stored.payload)
    ) return null;
    return stored as StoredStart;
  } catch {
    return null;
  }
}

function isStartPayload(value: unknown): value is StartPayload {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const payload = value as Record<string, unknown>;
  if (
    Object.keys(payload).length !== 5 ||
    typeof payload.topic !== "string" ||
    payload.topic !== payload.topic.trim() ||
    payload.topic.length === 0 ||
    codePoints(payload.topic) > 500 ||
    !Array.isArray(payload.queries) ||
    payload.queries.length < 1 ||
    payload.queries.length > 8 ||
    !payload.queries.every(
      (item) =>
        typeof item === "string" &&
        item === item.trim() &&
        item.length > 0 &&
        codePoints(item) <= 500,
    ) ||
    new Set(payload.queries).size !== payload.queries.length ||
    !Array.isArray(payload.languages) ||
    payload.languages.length > 20 ||
    !payload.languages.every(
      (item) => typeof item === "string" && LANGUAGE.test(item),
    ) ||
    new Set(payload.languages).size !== payload.languages.length ||
    !PROFILES.has(payload.freshness_profile as FreshnessProfile) ||
    typeof payload.idempotency_key !== "string" ||
    payload.idempotency_key.length < 8 ||
    payload.idempotency_key.length > 200
  ) return false;
  return true;
}

function clearStoredStart(): void {
  try {
    window.sessionStorage.removeItem(START_STORAGE_KEY);
  } catch {
    // Navigation is still safe after the successful server response.
  }
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

function codePoints(value: string): number {
  return [...value].length;
}

function startError(error: unknown): string {
  const code = publicCode(error);
  if (code === "idempotency_conflict") return "This request identity conflicts with another payload. Review the form and submit a new request.";
  if (code === "invalid_request") return "The server rejected these research fields. Review them before a new submission.";
  return "The response was not confirmed. Retry explicitly to reuse the same request identity.";
}

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
