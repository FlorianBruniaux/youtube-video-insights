import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiGetResponse, SearchResponse } from "../src/lib/types";
import { attachSearchPage } from "../src/lib/pages/search";

const searchFixture: SearchResponse = {
  schema_version: 1,
  hits: [
    {
      passage_id: "a".repeat(64),
      rank: 1,
      score: -2.5,
      channel_id: "channel-1",
      channel: "Local AI Lab",
      title: "Run models on your laptop",
      language: "en",
      excerpt: "<img src=x onerror=alert(1)> Keep the evidence local.",
      start_seconds: 83,
      end_seconds: 106,
      url: "https://www.youtube.com/watch?v=abc123DEF45&t=83s",
    },
  ],
  returned: 1,
  truncated: false,
};

function renderSearchDom(): HTMLElement {
  document.body.innerHTML = `
    <main data-search-page>
      <form data-search-form>
        <input name="q" />
        <input name="channel" />
        <input name="language" />
        <select name="limit"><option value="10">10</option><option value="20">20</option></select>
        <button type="submit">Search</button>
      </form>
      <p data-search-state role="status"></p>
      <section data-search-summary tabindex="-1"></section>
      <div data-search-results></div>
    </main>`;
  const root = document.querySelector<HTMLElement>("[data-search-page]");
  if (!root) throw new Error("search fixture missing");
  return root;
}

function submit(root: HTMLElement): void {
  root.querySelector("form")?.dispatchEvent(
    new SubmitEvent("submit", { bubbles: true, cancelable: true }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
  document.body.replaceChildren();
});

describe("corpus search page", () => {
  it("keeps GET filters in the URL, focuses the summary, and renders safe timestamp links", async () => {
    const root = renderSearchDom();
    const read = vi.fn().mockResolvedValue(searchFixture);
    attachSearchPage(root, read);
    root.querySelector<HTMLInputElement>("[name=q]")!.value = "local inference";
    root.querySelector<HTMLInputElement>("[name=channel]")!.value = "Local AI Lab";
    root.querySelector<HTMLInputElement>("[name=language]")!.value = "en";
    root.querySelector<HTMLSelectElement>("[name=limit]")!.value = "20";
    submit(root);
    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    await vi.waitFor(() =>
      expect(document.activeElement).toBe(root.querySelector("[data-search-summary]")),
    );

    expect(window.location.search).toBe(
      "?q=local+inference&channel=Local+AI+Lab&language=en&limit=20",
    );
    expect(read.mock.calls[0]?.[0]).toBe(
      "/api/v1/search?q=local+inference&channel=Local+AI+Lab&language=en&limit=20",
    );
    expect(root.querySelector("[data-search-results]")?.textContent).toContain(
      "<img src=x onerror=alert(1)>",
    );
    expect(root.querySelector("[data-search-results] img")).toBeNull();
    const link = root.querySelector<HTMLAnchorElement>("[data-timestamp-link]");
    expect(link?.href).toBe(
      "https://www.youtube.com/watch?v=abc123DEF45&t=83s",
    );
    expect(link?.rel).toBe("noopener noreferrer");
    expect(link?.target).toBe("_blank");
  });

  it("aborts the prior request and ignores its late result", async () => {
    const root = renderSearchDom();
    const resolvers: Array<(value: ApiGetResponse) => void> = [];
    const signals: AbortSignal[] = [];
    const read = vi.fn((_path: string, signal?: AbortSignal) => {
      if (signal) signals.push(signal);
      return new Promise<ApiGetResponse>((resolve) => resolvers.push(resolve));
    });
    attachSearchPage(root, read);
    const query = root.querySelector<HTMLInputElement>("[name=q]")!;

    query.value = "first";
    submit(root);
    query.value = "second";
    submit(root);

    expect(signals[0]?.aborted).toBe(true);
    resolvers[1]?.({ ...searchFixture, hits: [], returned: 0 });
    await vi.waitFor(() =>
      expect(root.querySelector("[data-search-summary]")?.textContent).toContain(
        "No passages",
      ),
    );
    resolvers[0]?.(searchFixture);
    await Promise.resolve();
    expect(root.querySelector("[data-search-results]")?.textContent).not.toContain(
      "Run models on your laptop",
    );
  });

  it("rejects an empty query without a request", async () => {
    const root = renderSearchDom();
    const read = vi.fn();
    attachSearchPage(root, read);

    submit(root);

    expect(read).not.toHaveBeenCalled();
    expect(root.querySelector("[data-search-state]")?.textContent).toContain(
      "Enter a search query",
    );
  });

  it.each([
    ["search_unavailable", "Search index is unavailable"],
    ["unexpected_response", "Cannot reach the local server"],
  ])("shows a bounded %s failure", async (code, expected) => {
    const root = renderSearchDom();
    const read = vi.fn().mockRejectedValue({ code });
    attachSearchPage(root, read);
    root.querySelector<HTMLInputElement>("[name=q]")!.value = "local";

    submit(root);

    await vi.waitFor(() =>
      expect(root.querySelector("[data-search-state]")?.textContent).toContain(
        expected,
      ),
    );
    expect(root.textContent).not.toContain("/Users/");
  });
});
