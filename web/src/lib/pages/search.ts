import { apiGet } from "../api";
import { createYouTubeWatchLink, replaceChildren, setText } from "../dom";
import { translate } from "../i18n";
import type { ApiGetResponse, ApiPath, SearchHit, SearchResponse } from "../types";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;

export function attachSearchPage(
  root: HTMLElement,
  read: ReadApi = (path, signal) => apiGet(path as ApiPath, signal),
): () => void {
  const form = requireElement<HTMLFormElement>(root, "[data-search-form]");
  const state = requireElement<HTMLElement>(root, "[data-search-state]");
  const summary = requireElement<HTMLElement>(root, "[data-search-summary]");
  const results = requireElement<HTMLElement>(root, "[data-search-results]");
  let active: AbortController | null = null;
  let requestNumber = 0;

  restoreForm(form, new URLSearchParams(window.location.search));

  const run = async (updateHistory: boolean): Promise<void> => {
    active?.abort();
    active = null;
    const currentRequest = ++requestNumber;
    const params = searchParameters(form);
    if ((params.get("q") ?? "").trim() === "") {
      setText(state, "Enter a search query to search the local corpus.");
      replaceChildren(summary, []);
      replaceChildren(results, []);
      const query = form.elements.namedItem("q");
      if (query instanceof HTMLInputElement) query.focus();
      return;
    }

    const controller = new AbortController();
    active = controller;
    if (updateHistory) {
      window.history.pushState({}, "", `${window.location.pathname}?${params}`);
    }
    setText(state, "Searching the local corpus…");
    replaceChildren(summary, []);
    replaceChildren(results, []);

    try {
      const response = (await read(
        `/api/v1/search?${params}`,
        controller.signal,
      )) as SearchResponse;
      if (currentRequest !== requestNumber || controller.signal.aborted) return;
      renderSearchResponse(summary, results, response);
      setText(state, "");
      summary.focus();
    } catch (error: unknown) {
      if (currentRequest !== requestNumber || isAbort(error)) return;
      setText(state, searchErrorMessage(error));
      replaceChildren(summary, []);
      replaceChildren(results, []);
    }
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void run(true);
  };
  const onPopState = (): void => {
    restoreForm(form, new URLSearchParams(window.location.search));
    void run(false);
  };
  form.addEventListener("submit", onSubmit);
  window.addEventListener("popstate", onPopState);

  if ((new URLSearchParams(window.location.search).get("q") ?? "").trim()) {
    void run(false);
  }

  return () => {
    active?.abort();
    form.removeEventListener("submit", onSubmit);
    window.removeEventListener("popstate", onPopState);
  };
}

function searchParameters(form: HTMLFormElement): URLSearchParams {
  const data = new FormData(form);
  const params = new URLSearchParams();
  for (const name of ["q", "channel", "language"] as const) {
    const value = String(data.get(name) ?? "").trim();
    if (value) params.set(name, value);
  }
  const limit = String(data.get("limit") ?? "10");
  params.set("limit", limit === "20" ? "20" : "10");
  return params;
}

function restoreForm(form: HTMLFormElement, params: URLSearchParams): void {
  for (const name of ["q", "channel", "language"] as const) {
    const field = form.elements.namedItem(name);
    if (field instanceof HTMLInputElement) field.value = params.get(name) ?? "";
  }
  const limit = form.elements.namedItem("limit");
  if (limit instanceof HTMLSelectElement) {
    limit.value = params.get("limit") === "20" ? "20" : "10";
  }
}

function renderSearchResponse(
  summary: HTMLElement,
  results: HTMLElement,
  response: SearchResponse,
): void {
  const heading = document.createElement("h2");
  heading.textContent = response.returned === 0
    ? translate("No passages found")
    : translate(
      response.returned === 1 ? "{count} passage found" : "{count} passages found",
      undefined,
      { count: response.returned },
    );
  const detail = document.createElement("p");
  detail.textContent = translate(response.truncated
    ? "Showing the first matching passages. Narrow the query for a more precise result."
    : "Results come from the local transcript index.");
  replaceChildren(summary, [heading, detail]);
  replaceChildren(results, response.hits.map(renderPassage));
}

function renderPassage(hit: SearchHit): HTMLElement {
  const article = document.createElement("article");
  article.className = "passage-card";
  const heading = document.createElement("h3");
  heading.textContent = hit.title;
  const metadata = document.createElement("p");
  metadata.className = "passage-meta";
  metadata.textContent = `${hit.channel} · ${hit.language}`;
  const excerpt = document.createElement("p");
  excerpt.textContent = hit.excerpt;
  const link = createYouTubeWatchLink(formatTimestamp(hit.start_seconds), hit.url);
  if (link !== null) {
    link.dataset.timestampLink = "";
    link.className = "passage-link";
  }
  replaceChildren(article, [heading, metadata, excerpt, ...(link ? [link] : [])]);
  return article;
}

function formatTimestamp(seconds: number): string {
  const rounded = Math.floor(seconds);
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return translate("Open at {timestamp}", undefined, {
    timestamp: `${minutes}:${String(remainder).padStart(2, "0")}`,
  });
}

function searchErrorMessage(error: unknown): string {
  const code = publicCode(error);
  if (code === "search_unavailable") {
    return translate("Search index is unavailable. Build or refresh the local index, then try again.");
  }
  if (code === "invalid_request") {
    return translate("The search filters are invalid. Check the query and try again.");
  }
  return translate("Cannot reach the local server. Check that YT Insights is running.");
}

function publicCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
