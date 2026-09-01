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

const SOURCE_ATTEMPT_STORAGE_KEY = "yt-insights:source-attempt:v2";
const LEGACY_SOURCE_ATTEMPT_STORAGE_KEY = "yt-insights:source-attempt:v1";

const sourcesFixture: SourcesResponse = {
  schema_version: 1,
  items: [
    {
      video_id: "abc123DEF45",
      title: "Local inference explained",
      published_at: "2026-08-20T10:00:00Z",
      languages: ["en"],
      sources: ["channel", "<img src=x onerror=alert(1)>"],
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
        <button data-source-preview-submit type="submit">Preview source</button>
      </form>
      <section data-source-plan hidden aria-labelledby="source-plan-title"></section>
      <button data-source-acquire type="button" hidden>Acquire these videos</button>
      <div data-source-job-region>
        <div data-source-job role="status"></div>
        <p>Job <code data-source-job-id></code></p>
        <button data-source-continue type="button" hidden>Continue checking</button>
        <button data-source-retry-admission type="button" hidden>Retry admission</button>
      </div>
    </main>`;
  const root = document.querySelector<HTMLElement>("[data-sources-page]");
  if (!root) throw new Error("sources fixture missing");
  return root;
}

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
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
    expect(root.querySelector<HTMLButtonElement>("[data-source-prev]")?.disabled).toBe(true);
    expect(root.querySelector<HTMLButtonElement>("[data-source-next]")?.disabled).toBe(true);
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
    expect(root.querySelector("[data-source-list]")?.textContent).toContain(
      "Published: 2026-08-20",
    );
    expect(root.querySelector("[data-source-list]")?.textContent).toContain(
      "Sources: channel",
    );
    expect(root.querySelector("[data-source-list] img")).toBeNull();
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
      .mockResolvedValueOnce(job("source_preview", preview, "preview-job"))
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
        }, "acquire-job"),
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

  it("disables preview admission and persists only the accepted job identity", async () => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const never = new Promise<ApiGetResponse>(() => undefined);
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockReturnValueOnce(never);
    const write = vi.fn().mockResolvedValue({
      schema_version: 1,
      job_id: "preview-job-safe",
    });
    attachSourcesPage(root, { read, write, wait: vi.fn() });

    const form = root.querySelector<HTMLFormElement>("form")!;
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(write).toHaveBeenCalledOnce());
    expect(root.querySelector<HTMLButtonElement>("[data-source-preview-submit]")?.disabled).toBe(true);
    expect(root.querySelector("[data-source-job-id]")?.textContent).toBe(
      "preview-job-safe",
    );
    const stored = window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY);
    expect(stored).toBe(
      '{"version":2,"stage":"polling","job_id":"preview-job-safe","kind":"source_preview","fingerprint":null,"idempotency_key":null}',
    );
    expect(stored).not.toContain("token");
  });

  it("resumes a stored preview job after reload without resubmitting it", async () => {
    window.sessionStorage.setItem(
      SOURCE_ATTEMPT_STORAGE_KEY,
      '{"version":2,"stage":"polling","job_id":"preview-resume","kind":"source_preview","fingerprint":null,"idempotency_key":null}',
    );
    const root = renderSourcesDom();
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce({
        ...job("source_preview", preview),
        job: { ...job("source_preview", preview).job, job_id: "preview-resume" },
      });
    const write = vi.fn();

    attachSourcesPage(root, { read, write, wait: vi.fn() });

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-plan]")?.textContent).toContain(
        "abc123DEF45",
      ),
    );
    expect(write).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledWith(
      "/api/v1/jobs/preview-resume",
      expect.any(AbortSignal),
    );
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
  });

  it("keeps a migrated legacy acquisition resumable across another reload", async () => {
    window.sessionStorage.setItem(
      LEGACY_SOURCE_ATTEMPT_STORAGE_KEY,
      `{"version":1,"job_id":"legacy-acquire","kind":"source_acquisition","fingerprint":"${"a".repeat(64)}","idempotency_key":"web-source-old-random"}`,
    );
    const firstRoot = renderSourcesDom();
    const pending = new Promise<ApiGetResponse>(() => undefined);
    const firstRead = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockReturnValueOnce(pending);
    const detach = attachSourcesPage(firstRoot, {
      read: firstRead,
      write: vi.fn(),
      wait: vi.fn(),
    });
    await vi.waitFor(() => expect(firstRead).toHaveBeenCalledTimes(2));
    detach();
    expect(window.sessionStorage.getItem(LEGACY_SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
    expect(JSON.parse(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY) ?? "null")).toMatchObject({
      version: 2,
      stage: "polling",
      job_id: "legacy-acquire",
      idempotency_key: "web-source-old-random",
    });

    const secondRoot = renderSourcesDom();
    const secondRead = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(
        job("source_acquisition", {
          selected: 1,
          transcripts_ready: 1,
          insights_ready: 0,
          failure_count: 0,
          exclusion_count: 0,
          items: [{
            video_id: "abc123DEF45",
            status: "acquired",
            error_code: null,
            source_sha256: "b".repeat(64),
          }],
          exit_code: 0,
        }, "legacy-acquire"),
      );
    attachSourcesPage(secondRoot, {
      read: secondRead,
      write: vi.fn(),
      wait: vi.fn(),
    });

    await vi.waitFor(() =>
      expect(secondRoot.querySelector("[data-source-job]")?.textContent).toContain(
        "1 transcripts ready",
      ),
    );
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
  });

  it("persists the acquisition fingerprint and the admitted idempotency key", async () => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const runningAcquisition = {
      schema_version: 1,
      job: {
        job_id: "acquire-slow",
        kind: "source_acquisition",
        status: "running",
        result: null,
        error_code: null,
      },
    } as const;
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(job("source_preview", preview, "preview-job"));
    for (let index = 0; index < 60; index += 1) {
      read.mockResolvedValueOnce(runningAcquisition);
    }
    const write = vi
      .fn()
      .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" })
      .mockResolvedValueOnce({ schema_version: 1, job_id: "acquire-slow" });
    attachSourcesPage(root, { read, write, wait: vi.fn() });
    root.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false),
    );

    root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-continue]")?.hidden).toBe(false),
    );
    const admittedBody = write.mock.calls[1]?.[1] as {
      fingerprint: string;
      idempotency_key: string;
    };
    expect(JSON.parse(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY) ?? "null")).toEqual({
      version: 2,
      stage: "polling",
      job_id: "acquire-slow",
      kind: "source_acquisition",
      fingerprint: "a".repeat(64),
      idempotency_key: admittedBody.idempotency_key,
    });
  });

  it("persists admission before POST and retries a lost response with the exact identity", async () => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const runningAcquisition = {
      schema_version: 1,
      job: {
        job_id: "acquire-recovered",
        kind: "source_acquisition",
        status: "running",
        result: null,
        error_code: null,
      },
    } as const;
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(job("source_preview", preview, "preview-job"));
    for (let index = 0; index < 60; index += 1) {
      read.mockResolvedValueOnce(runningAcquisition);
    }
    let rejectAdmission: (reason?: unknown) => void = () => undefined;
    const lostResponse = new Promise<never>((_resolve, reject) => {
      rejectAdmission = reject;
    });
    const write = vi
      .fn()
      .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" })
      .mockReturnValueOnce(lostResponse)
      .mockResolvedValueOnce({ schema_version: 1, job_id: "acquire-recovered" });
    attachSourcesPage(root, { read, write, wait: vi.fn() });
    root.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false),
    );

    root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    const expectedBody = {
      fingerprint: "a".repeat(64),
      idempotency_key: `web-source-acquire-${"a".repeat(64)}`,
    };
    expect(write.mock.calls[1]?.[1]).toEqual(expectedBody);
    expect(JSON.parse(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY) ?? "null")).toEqual({
      version: 2,
      stage: "admitting",
      job_id: null,
      kind: "source_acquisition",
      fingerprint: "a".repeat(64),
      idempotency_key: expectedBody.idempotency_key,
    });

    rejectAdmission(new TypeError("response lost"));
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-retry-admission]")?.hidden).toBe(false),
    );
    expect(write).toHaveBeenCalledTimes(2);

    root.querySelector<HTMLButtonElement>("[data-source-retry-admission]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(3));
    expect(write.mock.calls[2]?.[1]).toEqual(expectedBody);
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-continue]")?.hidden).toBe(false),
    );
    expect(JSON.parse(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY) ?? "null")).toMatchObject({
      version: 2,
      stage: "polling",
      job_id: "acquire-recovered",
      fingerprint: "a".repeat(64),
      idempotency_key: expectedBody.idempotency_key,
    });
  });

  it("does not submit acquisition when its admission identity cannot be persisted", async () => {
    const root = renderSourcesDom();
    root.querySelector<HTMLInputElement>("[name=source]")!.value =
      "https://www.youtube.com/@example";
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(job("source_preview", preview, "preview-job"));
    const write = vi
      .fn()
      .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" });
    attachSourcesPage(root, { read, write, wait: vi.fn() });
    root.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false),
    );
    const storageWrite = vi.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });

    root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();
    storageWrite.mockRestore();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-job]")?.textContent).toContain(
        "could not be saved",
      ),
    );
    expect(write).toHaveBeenCalledOnce();
    expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false);
  });

  it("uses one acquisition identity across tabs and changes it with the preview", async () => {
    const admitInFreshTab = async (plan: SourcePreviewResult): Promise<unknown> => {
      window.sessionStorage.clear();
      const root = renderSourcesDom();
      root.querySelector<HTMLInputElement>("[name=source]")!.value =
        "https://www.youtube.com/@example";
      const read = vi
        .fn()
        .mockResolvedValueOnce(sourcesFixture)
        .mockResolvedValueOnce(job("source_preview", plan, "preview-job"));
      const write = vi
        .fn()
        .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" })
        .mockRejectedValueOnce(new TypeError("response lost"));
      const detach = attachSourcesPage(root, { read, write, wait: vi.fn() });
      root.querySelector("form")?.dispatchEvent(
        new SubmitEvent("submit", { bubbles: true, cancelable: true }),
      );
      await vi.waitFor(() =>
        expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false),
      );
      root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();
      await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
      const body = write.mock.calls[1]?.[1];
      detach();
      return body;
    };

    const firstTab = await admitInFreshTab(preview);
    const secondTab = await admitInFreshTab(preview);
    const changedPreview = { ...preview, fingerprint: "b".repeat(64) };
    const changedTab = await admitInFreshTab(changedPreview);

    expect(firstTab).toEqual(secondTab);
    expect(firstTab).toEqual({
      fingerprint: "a".repeat(64),
      idempotency_key: `web-source-acquire-${"a".repeat(64)}`,
    });
    expect(changedTab).toEqual({
      fingerprint: "b".repeat(64),
      idempotency_key: `web-source-acquire-${"b".repeat(64)}`,
    });
  });

  it("clears an evicted job and requires a new explicit preview", async () => {
    window.sessionStorage.setItem(
      SOURCE_ATTEMPT_STORAGE_KEY,
      '{"version":2,"stage":"polling","job_id":"preview-evicted","kind":"source_preview","fingerprint":null,"idempotency_key":null}',
    );
    const root = renderSourcesDom();
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockRejectedValueOnce({ code: "not_found" });
    const write = vi.fn();

    attachSourcesPage(root, { read, write, wait: vi.fn() });

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-job]")?.textContent).toContain(
        "no longer available",
      ),
    );
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-source-preview-submit]")?.disabled).toBe(false);
    expect(root.querySelector<HTMLButtonElement>("[data-source-continue]")?.hidden).toBe(true);
    expect(write).not.toHaveBeenCalled();
  });

  it.each(["plan_changed", "stale_revision"])(
    "clears an admitting acquisition after %s",
    async (code) => {
      const root = renderSourcesDom();
      root.querySelector<HTMLInputElement>("[name=source]")!.value =
        "https://www.youtube.com/@example";
      const read = vi
        .fn()
        .mockResolvedValueOnce(sourcesFixture)
        .mockResolvedValueOnce(job("source_preview", preview, "preview-job"));
      const write = vi
        .fn()
        .mockResolvedValueOnce({ schema_version: 1, job_id: "preview-job" })
        .mockRejectedValueOnce({ code });
      attachSourcesPage(root, { read, write, wait: vi.fn() });
      root.querySelector("form")?.dispatchEvent(
        new SubmitEvent("submit", { bubbles: true, cancelable: true }),
      );
      await vi.waitFor(() =>
        expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.disabled).toBe(false),
      );

      root.querySelector<HTMLButtonElement>("[data-source-acquire]")!.click();

      await vi.waitFor(() =>
        expect(root.querySelector("[data-source-job]")?.textContent).toContain(
          "Preview the source again",
        ),
      );
      expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
      expect(root.querySelector<HTMLButtonElement>("[data-source-preview-submit]")?.disabled).toBe(false);
      expect(root.querySelector<HTMLButtonElement>("[data-source-retry-admission]")?.hidden).toBe(true);
      expect(root.querySelector<HTMLButtonElement>("[data-source-acquire]")?.hidden).toBe(true);
    },
  );

  it("keeps a timed-out job and continues polling the same ID only after confirmation", async () => {
    window.sessionStorage.setItem(
      SOURCE_ATTEMPT_STORAGE_KEY,
      '{"version":2,"stage":"polling","job_id":"preview-slow","kind":"source_preview","fingerprint":null,"idempotency_key":null}',
    );
    const root = renderSourcesDom();
    const running = {
      schema_version: 1,
      job: {
        job_id: "preview-slow",
        kind: "source_preview",
        status: "running",
        result: null,
        error_code: null,
      },
    } as const;
    const completed = {
      ...job("source_preview", preview),
      job: { ...job("source_preview", preview).job, job_id: "preview-slow" },
    };
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(running);
    for (let index = 1; index < 60; index += 1) read.mockResolvedValueOnce(running);
    read.mockResolvedValueOnce(completed);
    const write = vi.fn();
    attachSourcesPage(root, { read, write, wait: vi.fn() });

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-source-continue]")?.hidden).toBe(false),
    );
    expect(root.querySelector("[data-source-job-id]")?.textContent).toBe("preview-slow");
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).not.toBeNull();
    expect(read).toHaveBeenCalledTimes(61);
    expect(write).not.toHaveBeenCalled();

    root.querySelector<HTMLButtonElement>("[data-source-continue]")!.click();

    await vi.waitFor(() =>
      expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull(),
    );
    expect(read).toHaveBeenLastCalledWith(
      "/api/v1/jobs/preview-slow",
      expect.any(AbortSignal),
    );
    expect(write).not.toHaveBeenCalled();
  });

  it("rejects a mismatched returned job without dropping the resumable attempt", async () => {
    window.sessionStorage.setItem(
      SOURCE_ATTEMPT_STORAGE_KEY,
      '{"version":2,"stage":"polling","job_id":"preview-expected","kind":"source_preview","fingerprint":null,"idempotency_key":null}',
    );
    const root = renderSourcesDom();
    const read = vi
      .fn()
      .mockResolvedValueOnce(sourcesFixture)
      .mockResolvedValueOnce(job("source_preview", preview));
    attachSourcesPage(root, { read, write: vi.fn(), wait: vi.fn() });

    await vi.waitFor(() =>
      expect(root.querySelector("[data-source-job]")?.textContent).toContain(
        "could not be verified",
      ),
    );
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).not.toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-source-continue]")?.hidden).toBe(false);
  });

  it("ignores an oversized persisted attempt without polling it", async () => {
    window.sessionStorage.setItem(SOURCE_ATTEMPT_STORAGE_KEY, "x".repeat(5_000));
    const root = renderSourcesDom();
    const read = vi.fn().mockResolvedValue(sourcesFixture);

    attachSourcesPage(root, { read, write: vi.fn(), wait: vi.fn() });

    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    expect(window.sessionStorage.getItem(SOURCE_ATTEMPT_STORAGE_KEY)).toBeNull();
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
          .mockResolvedValueOnce(jobError("source_preview", code, "preview-job"));
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

    const wait = vi.fn().mockResolvedValue(undefined);
    await expect(pollJob("job-1", read, wait)).rejects.toMatchObject({
      code: "poll_timeout",
    });
    expect(read).toHaveBeenCalledTimes(60);
    expect(wait.mock.calls.slice(0, 10).map(([delay]) => delay)).toEqual([
      500, 500, 500, 500, 500, 500, 500, 500, 500, 1_000,
    ]);
    expect(wait).toHaveBeenLastCalledWith(2_000);
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

  it("renders resumable research state, required action, and update time", async () => {
    document.body.innerHTML = `
      <main data-dashboard>
        <p data-dashboard-status></p>
        <div data-dashboard-metrics></div>
        <div data-dashboard-sessions></div>
        <div data-dashboard-exports></div>
      </main>`;
    const root = document.querySelector<HTMLElement>("[data-dashboard]")!;
    const read = vi
      .fn()
      .mockResolvedValueOnce({
        schema_version: 1,
        status: "ok",
        corpus: { health: "ready", videos: 1, transcripts: 1, documents_indexed: 1, passages_indexed: 2 },
      })
      .mockResolvedValueOnce({
        schema_version: 1,
        items: [{
          session_id: "session_1",
          topic: "Local inference",
          queries: ["local inference"],
          languages: ["en"],
          freshness_profile: "standard",
          discovery_fingerprint: "a".repeat(64),
          state: "awaiting_sufficiency_confirmation",
          revision: 2,
          retry_target: null,
          created_at: "2026-08-31T10:00:00Z",
          updated_at: "2026-09-01T09:30:00Z",
          required_user_action: "confirm_sufficiency_or_refresh",
        }],
        limit: 5,
        offset: 0,
      })
      .mockResolvedValueOnce({
        schema_version: 1,
        items: [],
        limit: 5,
        truncated: false,
        inventory_complete: true,
        inventory_examined: 0,
        inventory_limit: 32,
      });

    await attachDashboard(root, read);

    const sessions = root.querySelector("[data-dashboard-sessions]")?.textContent ?? "";
    expect(sessions).toContain("Awaiting sufficiency confirmation");
    expect(sessions).toContain("Needs your decision");
    expect(sessions).toContain("Updated 2026-09-01 09:30 UTC");
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

function job(
  kind: "source_preview" | "source_acquisition",
  result: unknown,
  jobId = "job-1",
): JobResponse {
  return {
    schema_version: 1,
    job: { job_id: jobId, kind, status: "succeeded", result, error_code: null } as JobResponse["job"],
  };
}

function jobError(kind: "source_preview", code: string, jobId = "job-1"): JobResponse {
  return job(kind, { schema_version: 1, error: { code } }, jobId);
}
