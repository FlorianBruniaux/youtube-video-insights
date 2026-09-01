import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ApiGetResponse,
  JobResponse,
  SourcePreviewResult,
  SourcesResponse,
  StatusResponse,
} from "../src/lib/types";
import { attachDashboard } from "../src/lib/pages/dashboard";
import { attachSourcesPage, pollJob } from "../src/lib/pages/sources";

const sourcesFixture: SourcesResponse = {
  schema_version: 1,
  items: [
    {
      video_id: "abc123DEF45",
      title: "Local inference explained",
      published_at: "2026-08-20T10:00:00Z",
      languages: ["en"],
      sources: ["channel"],
      url: "https://www.youtube.com/watch?v=abc123DEF45",
      artifact_count: 2,
      transcript_state: "available",
      index_state: "indexed",
    },
  ],
  limit: 20,
  offset: 0,
};

const preview: SourcePreviewResult = {
  fingerprint: "a".repeat(64),
  source_kind: "channel",
  selected_count: 2,
  video_ids: ["abc123DEF45", "xyz987QWE12"],
  videos: [
    {
      video_id: "abc123DEF45",
      title: "Local inference explained",
      published_at: "2026-08-20T10:00:00Z",
      url: "https://www.youtube.com/watch?v=abc123DEF45",
    },
    {
      video_id: "xyz987QWE12",
      title: "Production MLX",
      published_at: "2026-08-21T10:00:00Z",
      url: "https://www.youtube.com/watch?v=xyz987QWE12",
    },
  ],
  videos_returned: 2,
  videos_truncated: false,
  language: "en",
  analyze: false,
  requires_confirmation: true,
  excluded_count: 3,
  discovery_error_count: 1,
};

function renderSourcesDom(): HTMLElement {
  document.body.innerHTML = `
    <main data-sources-page>
      <p data-source-state role="status"></p>
      <div data-source-list></div>
      <button data-source-prev type="button">Previous</button>
      <button data-source-next type="button">Next</button>
      <span data-source-page-label></span>
      <form data-source-preview-form>
        <input name="source" type="url" />
        <input name="language" value="en" />
        <input name="analyze" type="checkbox" />
        <button type="submit">Preview source</button>
      </form>
      <section data-source-plan hidden aria-labelledby="source-plan-title"></section>
      <button data-source-acquire type="button" hidden>Acquire these videos</button>
      <div data-source-job role="status"></div>
    </main>`;
  const root = document.querySelector<HTMLElement>("[data-sources-page]");
  if (!root) throw new Error("sources fixture missing");
  return root;
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("source inventory and acquisition", () => {
  it("keeps the loading state until the first bounded page settles", () => {
    const root = renderSourcesDom();
    attachSourcesPage(root, {
      read: vi.fn(() => new Promise<ApiGetResponse>(() => undefined)),
      write: vi.fn(),
      wait: vi.fn(),
    });

    expect(root.querySelector("[data-source-state]")?.textContent).toContain(
      "Loading a bounded source page",
    );
  });

  it("loads a bounded page and exposes safe responsive inventory markup", async () => {
    const root = renderSourcesDom();
    const read = vi.fn().mockResolvedValue(sourcesFixture);

    attachSourcesPage(root, { read, write: vi.fn(), wait: vi.fn() });

    await vi.waitFor(() =>
      expect(read).toHaveBeenCalledWith(
        "/api/v1/sources?limit=20&offset=0",
        expect.any(AbortSignal),
      ),
    );
    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-list]")?.textContent).toContain(
        "Local inference explained",
      ),
    );
    expect(root.querySelector("table")).not.toBeNull();
    expect(root.querySelector("[data-source-cards]")).not.toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-source-prev]")?.disabled).toBe(true);
    expect(root.querySelector<HTMLButtonElement>("[data-source-next]")?.disabled).toBe(true);
    expect(root.querySelectorAll("a")[0]?.rel).toBe("noopener noreferrer");
  });

  it("renders an empty first page without an inverted range", async () => {
    const root = renderSourcesDom();
    attachSourcesPage(root, {
      read: vi.fn().mockResolvedValue({ ...sourcesFixture, items: [] }),
      write: vi.fn(),
      wait: vi.fn(),
    });

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-page-label]")?.textContent).toBe(
        "No sources",
      ),
    );
    expect(root.querySelector("[data-source-list]")?.textContent).toContain(
      "No sources found",
    );
  });

  it("requests only the next bounded page and enables return navigation", async () => {
    const root = renderSourcesDom();
    const fullPage = {
      ...sourcesFixture,
      items: Array.from({ length: 20 }, (_, index) => ({
        ...sourcesFixture.items[0]!,
        video_id: `${String(index).padStart(11, "0")}`,
        url: `https://www.youtube.com/watch?v=${String(index).padStart(11, "0")}`,
      })),
    } satisfies SourcesResponse;
    const read = vi
      .fn()
      .mockResolvedValueOnce(fullPage)
      .mockResolvedValueOnce({ ...sourcesFixture, items: [], offset: 20 });
    attachSourcesPage(root, { read, write: vi.fn(), wait: vi.fn() });
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-next]")?.disabled).toBe(false),
    );

    root.querySelector<HTMLButtonElement>("[data-source-next]")!.click();

    await vi.waitFor(() =>
      expect(read).toHaveBeenLastCalledWith(
        "/api/v1/sources?limit=20&offset=20",
        expect.any(AbortSignal),
      ),
    );
    expect(root.querySelector<HTMLButtonElement>("[data-source-prev]")?.disabled).toBe(false);
  });

  it("requires preview, shows every exact selected ID, then acquires explicitly", async () => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(job("source_preview", preview))
      .mockResolvedValueOnce(
        job("source_acquisition", {
          selected: 2,
          transcripts_ready: 2,
          insights_ready: 0,
          failure_count: 0,
          exclusion_count: 3,
          items: [
            { video_id: "abc123DEF45", status: "acquired", error_code: null, source_sha256: "b".repeat(64) },
            { video_id: "xyz987QWE12", status: "acquired", error_code: null, source_sha256: "c".repeat(64) },
          ],
          exit_code: 0,
        }),
      );
    const write = vi
      .fn()
      .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" })
      .mockResolvedValueOnce({ schema_version: 1, job_id: "acquire-job" });
    attachSourcesPage(root, { read, write, wait: vi.fn() });

    root.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-plan]")?.textContent).toContain(
        "abc123DEF45",
      ),
    );
    expect(root.querySelector("[data-source-plan]")?.textContent).toContain(
      "xyz987QWE12",
    );
    const planRegion = root.querySelector<HTMLElement>("[data-source-plan]")!;
    expect(planRegion.getAttribute("aria-labelledby")).toBe("source-plan-title");
    expect(planRegion.querySelector("#source-plan-title")).not.toBeNull();
    expect(root.querySelector("[data-source-plan]")?.textContent).not.toContain(
      "/Users/",
    );
    expect(write).toHaveBeenCalledTimes(1);

    root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();
    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    expect(write.mock.calls[1]?.[0]).toBe("/api/v1/sources/acquire");
    expect(write.mock.calls[1]?.[1]).toEqual({
      fingerprint: "a".repeat(64),
      idempotency_key: expect.stringMatching(/^web-source-/),
    });
    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-job]")?.textContent).toContain(
        "2 transcripts ready",
      ),
    );
  });

  it.each([
    ["plan_too_large", "too large"],
    ["stale_revision", "changed"],
    ["forbidden", "permission"],
  ])("renders %s without automatically retrying", async (code, message) => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const read = code === "forbidden"
      ? vi.fn().mockResolvedValueOnce(sourcesFixture)
      : vi
          .fn()
          .mockResolvedValueOnce(sourcesFixture)
          .mockResolvedValueOnce(jobError("source_preview", code));
    const write = vi
      .fn()
      .mockImplementation(() => code === "forbidden"
        ? Promise.reject({ code: "forbidden" })
        : Promise.resolve({ schema_version: 1, job_id: "preview-job" }));
    attachSourcesPage(root, { read, write, wait: vi.fn() });

    root.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-job]")?.textContent.toLowerCase()).toContain(
        message,
      ),
    );
    expect(write).toHaveBeenCalledOnce();
    expect(read).toHaveBeenCalledTimes(code === "forbidden" ? 1 : 2);
  });
});

describe("bounded job polling", () => {
  it("stops after the fixed attempt budget", async () => {
    const read = vi.fn().mockResolvedValue({
      schema_version: 1,
      job: { job_id: "job-1", kind: "source_preview", status: "running", result: null, error_code: null },
    });

    await expect(pollJob("job-1", read, vi.fn())).rejects.toMatchObject({
      code: "poll_timeout",
    });
    expect(read).toHaveBeenCalledTimes(8);
  });
});

describe("progressive dashboard", () => {
  it("keeps healthy corpus metrics when a secondary panel fails", async () => {
    document.body.innerHTML = `
      <main data-dashboard>
        <p data-dashboard-status></p>
        <div data-dashboard-metrics></div>
        <div data-dashboard-sessions></div>
        <div data-dashboard-exports></div>
      </main>`;
    const root = document.querySelector<HTMLElement>("[data-dashboard]")!;
    const status: StatusResponse = {
      schema_version: 1,
      status: "ok",
      corpus: { health: "ready", videos: 12, transcripts: 10, documents_indexed: 10, passages_indexed: 42 },
    };
    const read = vi.fn((path: string): Promise<ApiGetResponse> => {
      if (path === "/api/v1/status") return Promise.resolve(status);
      if (path.startsWith("/api/v1/research/sessions")) return Promise.reject({ code: "research_unavailable" });
      return Promise.resolve({
        schema_version: 1,
        items: [],
        limit: 5,
        truncated: false,
        inventory_complete: true,
        inventory_examined: 0,
        inventory_limit: 32,
      });
    });

    await attachDashboard(root, read);

    expect(root.querySelector("[data-dashboard-metrics]")?.textContent).toContain("42");
    expect(root.querySelector("[data-dashboard-sessions]")?.textContent).toContain("unavailable");
    expect(root.querySelector("[data-dashboard-exports]")?.textContent).toContain("No exports");
  });

  it("shows a bounded offline state without attempting secondary panels", async () => {
    document.body.innerHTML = `
      <main data-dashboard>
        <p data-dashboard-status></p>
        <div data-dashboard-metrics></div>
        <div data-dashboard-sessions></div>
        <div data-dashboard-exports></div>
      </main>`;
    const root = document.querySelector<HTMLElement>("[data-dashboard]")!;
    const read = vi.fn().mockRejectedValue(new TypeError("secret /Users/private"));

    await attachDashboard(root, read);

    expect(read).toHaveBeenCalledOnce();
    expect(root.textContent).toContain("Cannot reach the local YT Insights server");
    expect(root.textContent).not.toContain("/Users/private");
  });
});

function job(kind: "source_preview" | "source_acquisition", result: unknown): JobResponse {
  return {
    schema_version: 1,
    job: { job_id: "job-1", kind, status: "succeeded", result, error_code: null } as JobResponse["job"],
  };
}

function jobError(kind: "source_preview", code: string): JobResponse {
  return job(kind, { schema_version: 1, error: { code } });
}
