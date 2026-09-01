import { apiGet } from "../api";
import { replaceChildren, setText } from "../dom";
import { translate } from "../i18n";
import type {
  ApiGetResponse,
  ApiPath,
  ExportsResponse,
  ResearchListResponse,
  StatusResponse,
} from "../types";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;

export async function attachDashboard(
  root: HTMLElement,
  read: ReadApi = (path, signal) => apiGet(path as ApiPath, signal),
): Promise<void> {
  const statusTarget = requireElement<HTMLElement>(root, "[data-dashboard-status]");
  const metrics = requireElement<HTMLElement>(root, "[data-dashboard-metrics]");
  const sessions = requireElement<HTMLElement>(root, "[data-dashboard-sessions]");
  const exportsTarget = requireElement<HTMLElement>(root, "[data-dashboard-exports]");
  setText(statusTarget, "Checking the local corpus…");

  let status: StatusResponse;
  try {
    status = (await read("/api/v1/status")) as StatusResponse;
  } catch {
    setText(statusTarget, "Cannot reach the local YT Insights server.");
    replaceChildren(metrics, []);
    setText(sessions, "Recent research is unavailable.");
    setText(exportsTarget, "Recent exports are unavailable.");
    return;
  }

  setText(
    statusTarget,
    status.corpus.health === "ready"
      ? "Corpus ready"
      : "Corpus available, but the search index needs attention",
  );
  replaceChildren(metrics, [
    metric("Videos", status.corpus.videos),
    metric("Transcripts", status.corpus.transcripts),
    metric("Indexed passages", status.corpus.passages_indexed ?? "Unavailable"),
  ]);

  const [recentSessions, recentExports] = await Promise.allSettled([
    read("/api/v1/research/sessions?limit=5&offset=0"),
    read("/api/v1/exports?limit=5"),
  ]);
  if (recentSessions.status === "fulfilled") {
    renderSessions(sessions, recentSessions.value as ResearchListResponse);
  } else {
    setText(sessions, "Recent research is unavailable. Corpus status remains current.");
  }
  if (recentExports.status === "fulfilled") {
    renderExports(exportsTarget, recentExports.value as ExportsResponse);
  } else {
    setText(exportsTarget, "Recent exports are unavailable. Corpus status remains current.");
  }
}

function metric(label: string, value: string | number): HTMLElement {
  const card = document.createElement("article");
  card.className = "metric-card dashboard-metric";
  const name = document.createElement("p");
  name.className = "metric-label";
  name.textContent = translate(label);
  const count = document.createElement("p");
  count.className = "metric-value";
  count.textContent = String(value);
  replaceChildren(card, [name, count]);
  return card;
}

function renderSessions(target: HTMLElement, response: ResearchListResponse): void {
  if (response.items.length === 0) {
    setText(target, "No research sessions yet.");
    return;
  }
  const list = document.createElement("ul");
  for (const item of response.items) {
    const row = document.createElement("li");
    row.className = "dashboard-session";
    const link = document.createElement("a");
    link.href = `/research/${encodeURIComponent(item.session_id)}/`;
    link.textContent = item.topic;
    const metadata = document.createElement("p");
    metadata.className = "dashboard-session-meta";
    metadata.textContent = translate("{state} · Updated {date}", undefined, {
      state: stateLabel(item.state),
      date: formatUpdatedAt(item.updated_at),
    });
    row.append(link, metadata);
    if (item.required_user_action !== null) {
      const warning = document.createElement("p");
      warning.className = "dashboard-session-warning";
      warning.textContent = translate(
        item.required_user_action === "confirm_sufficiency_or_refresh"
          ? "Needs your decision: confirm the corpus is sufficient or refresh it."
          : "Needs your decision: approve candidates or cancel this research.",
      );
      row.append(warning);
    }
    list.append(row);
  }
  replaceChildren(target, [list]);
}

function stateLabel(value: string): string {
  const words = value.replaceAll("_", " ");
  const label = translate(words);
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}`;
}

function formatUpdatedAt(value: string): string {
  const dateAndTime = value.slice(0, 16).replace("T", " ");
  return `${dateAndTime} UTC`;
}

function renderExports(target: HTMLElement, response: ExportsResponse): void {
  if (response.items.length === 0) {
    setText(target, "No exports yet.");
    return;
  }
  const list = document.createElement("ul");
  for (const item of response.items) {
    const row = document.createElement("li");
    row.textContent = item.name;
    list.append(row);
  }
  replaceChildren(target, [list]);
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}
