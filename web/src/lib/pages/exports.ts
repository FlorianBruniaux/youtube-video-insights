import { apiGet } from "../api";
import { replaceChildren } from "../dom";
import type { ApiGetResponse, ApiPath, ExportItem, ExportsResponse } from "../types";

type ReadApi = (path: string, signal?: AbortSignal) => Promise<ApiGetResponse>;
const SAFE_OPEN_URL = /^\/api\/v1\/exports\/[0-9a-f]{64}\/dossier$/;

export function attachExportsPage(
  root: HTMLElement,
  read: ReadApi = (path, signal) => apiGet(path as ApiPath, signal),
): () => void {
  const status = requireElement<HTMLElement>(root, "[data-exports-status]");
  const list = requireElement<HTMLElement>(root, "[data-export-list]");
  const controller = new AbortController();
  status.textContent = "Loading the bounded export inventory…";
  void read("/api/v1/exports?limit=20", controller.signal)
    .then((response) => {
      if (controller.signal.aborted || !("items" in response)) return;
      const exports = response as ExportsResponse;
      renderExports(list, exports.items);
      status.textContent = exports.inventory_complete
        ? exports.items.length === 0
          ? "No exports yet."
          : `${exports.items.length} export${exports.items.length === 1 ? "" : "s"} available.`
        : `Showing a partial inventory after examining ${exports.inventory_examined} entries.`;
    })
    .catch((error: unknown) => {
      if (error instanceof Error && error.name === "AbortError") return;
      replaceChildren(list, []);
      status.textContent = "The local export inventory is unavailable.";
    });
  return () => controller.abort();
}

function renderExports(target: HTMLElement, items: readonly ExportItem[]): void {
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "Create an export from an eligible research workspace.";
    replaceChildren(target, [empty]);
    return;
  }
  const grid = document.createElement("div");
  grid.className = "export-grid";
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "export-card";
    const title = document.createElement("h2");
    title.textContent = item.name;
    const meta = document.createElement("p");
    meta.textContent = item.manifest_valid && item.created_at !== null
      ? `Created ${formatTimestamp(item.created_at)} · Session ${item.session_id ?? "unknown"}`
      : "Invalid manifest projection";
    card.append(title, meta);
    if (item.manifest_valid && item.open_url !== null && SAFE_OPEN_URL.test(item.open_url)) {
      const link = document.createElement("a");
      link.className = "button button-secondary";
      link.dataset.exportOpen = "";
      link.href = item.open_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open dossier";
      card.append(link);
    }
    grid.append(card);
  }
  replaceChildren(target, [grid]);
}

function formatTimestamp(value: string): string {
  return `${value.slice(0, 16).replace("T", " ")} UTC`;
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Missing page element: ${selector}`);
  return element;
}

