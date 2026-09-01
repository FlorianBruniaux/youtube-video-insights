import { afterEach, describe, expect, it, vi } from "vitest";

import { attachExportsPage } from "../src/lib/pages/exports";
import { attachResearchNewPage } from "../src/lib/pages/research-new";
import { attachResearchWorkspace } from "../src/lib/pages/research-workspace";
import {
  pollResearchJob,
  researchActionScope,
  researchScopeFingerprint,
} from "../src/lib/research-job";
import { createAttemptIdentityCoordinator } from "../src/lib/source-attempt-coordinator";
import type {
  ExportsResponse,
  JobResponse,
  ResearchResponse,
} from "../src/lib/types";

const sessionId = "research_session_1";

function session(
  state: ResearchResponse["session"]["state"],
  action: ResearchResponse["required_user_action"],
  revision = 1,
): ResearchResponse {
  return {
    schema_version: 1,
    session: {
      session_id: sessionId,
      topic: "Local inference",
      queries: ["mlx inference", "ollama performance"],
      languages: ["en", "fr"],
      freshness_profile: "standard",
      discovery_fingerprint: "a".repeat(64),
      state,
      revision,
      retry_target: state === "failed_retryable" ? "discovering" : null,
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-31T10:00:00Z",
    },
    assessment: {
      created_at: "2026-08-31T10:00:00Z",
      snapshot: {
        search_generation: "b".repeat(64),
        catalog_generation: "c".repeat(64),
      },
      coverage: {
        matched_passages: 1,
        matched_videos: 1,
        distinct_channels: 1,
        queries_with_zero_hits: ["ollama performance"],
        newest_source_published_at: "2026-08-20",
        unknown_publication_date_count: 0,
      },
      freshness: {
        profile: "standard",
        maximum_age_days: 30,
        last_successful_discovery_at: null,
        stale: true,
        reason: "The newest evidence is older than the selected window.",
      },
      passages: [
        {
          query: "mlx inference",
          passage_id: "d".repeat(64),
          video_id: "abc123DEF45",
          channel_id: "local-ai",
          rank: 1,
          url: "https://www.youtube.com/watch?v=abc123DEF45",
          excerpt: "<img src=x onerror=alert(1)> Local evidence.",
          source_sha256: "e".repeat(64),
        },
      ],
      videos: [
        {
          query: "mlx inference",
          video_id: "abc123DEF45",
          source_keys: ["channel:local-ai"],
          title: "Run MLX locally",
          published_at: "2026-08-20",
          rank: 1,
          watch_url: "https://www.youtube.com/watch?v=abc123DEF45",
        },
      ],
    },
    candidates:
      action === "approve_candidates_or_cancel"
        ? [
            {
              video_id: "xyz987QWE12",
              title: "Fresh local inference benchmark",
              channel_id: "bench-lab",
              channel_title: "Benchmark Lab",
              published_at: "2026-08-30",
              watch_url: "https://www.youtube.com/watch?v=xyz987QWE12",
              matched_queries: ["mlx inference"],
              original_rank: 1,
              status: "candidate",
            },
          ]
        : null,
    required_user_action: action,
    error_code: state === "failed_retryable" ? "discovery_unavailable" : null,
    acquisition_history: [],
    acquisition_history_truncated: false,
    history: {
      decisions: [
        { action: "refresh", created_at: "2026-08-31T10:01:00Z" },
      ],
      events: [
        {
          event_id: 1,
          from_state: "assessing",
          to_state: "awaiting_sufficiency_confirmation",
          event_code: "assessment_completed",
          created_at: "2026-08-31T10:00:00Z",
        },
      ],
      decisions_truncated: false,
      events_truncated: false,
    },
  } as ResearchResponse;
}

function renderNewDom(): HTMLElement {
  document.body.innerHTML = `
    <main data-research-new-page>
      <form data-research-new-form>
        <input name="topic" />
        <textarea name="queries"></textarea>
        <input name="languages" />
        <select name="freshness_profile">
          <option value="fast">Fast</option><option value="standard">Standard</option>
          <option value="stable">Stable</option><option value="historical">Historical</option>
        </select>
        <button data-research-new-submit type="submit">Create</button>
      </form>
      <p data-research-new-status></p>
      <button data-research-new-retry type="button" hidden>Retry the same request</button>
    </main>`;
  return document.querySelector<HTMLElement>("[data-research-new-page]")!;
}

function renderWorkspaceDom(): HTMLElement {
  document.body.innerHTML = `
    <main data-research-workspace>
      <p data-research-status role="status"></p>
      <header data-research-heading></header>
      <section data-evidence-panel></section>
      <div data-research-controls>
        <aside data-decision-panel></aside>
        <section data-job-progress hidden>
          <p data-job-message></p><code data-job-id></code>
          <button data-job-continue type="button" hidden>Continue checking</button>
          <button data-job-retry-admission type="button" hidden>Retry admission</button>
        </section>
      </div>
      <section data-candidate-list></section>
      <section data-acquisition-history></section>
      <details data-event-timeline><summary>Timeline</summary><div data-event-list></div></details>
    </main>`;
  return document.querySelector<HTMLElement>("[data-research-workspace]")!;
}

function renderExportsDom(): HTMLElement {
  document.body.innerHTML = `
    <main data-exports-page>
      <p data-exports-status></p>
      <div data-export-list></div>
    </main>`;
  return document.querySelector<HTMLElement>("[data-exports-page]")!;
}

function submit(form: HTMLFormElement): void {
  form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  Reflect.deleteProperty(window, "confirm");
  document.body.replaceChildren();
});

function serialLock(): <T>(name: string, task: () => T | Promise<T>) => Promise<T> {
  let tail = Promise.resolve();
  return async <T>(_name: string, task: () => T | Promise<T>): Promise<T> => {
    const previous = tail;
    let release = (): void => undefined;
    tail = new Promise<void>((resolve) => { release = resolve; });
    await previous;
    try {
      return await task();
    } finally {
      release();
    }
  };
}

describe("bounded research job polling", () => {
  it("binds the collision-resistant research scope to language and exact action fields", async () => {
    const english = researchActionScope(
      sessionId,
      "research_acquisition",
      7,
      "en",
    );
    const french = researchActionScope(
      sessionId,
      "research_acquisition",
      7,
      "fr",
    );

    expect(english).toBe(
      '["research_session_1","research_acquisition",7,"en"]',
    );
    expect(await researchScopeFingerprint(english)).toMatch(/^[0-9a-f]{64}$/);
    expect(await researchScopeFingerprint(english)).not.toBe(
      await researchScopeFingerprint(french),
    );
  });

  it("uses 0.5, 1, then 2 second backoff and validates the exact job identity", async () => {
    const delays: number[] = [];
    const read = vi
      .fn()
      .mockResolvedValueOnce(runningJob("queued"))
      .mockResolvedValueOnce(runningJob("running"))
      .mockResolvedValueOnce(job("research_discovery", "succeeded", session("awaiting_candidate_approval", "approve_candidates_or_cancel")));
    const controller = new AbortController();

    const result = await pollResearchJob(
      { version: 1, session_id: sessionId, job_id: "job_discovery_1", kind: "research_discovery" },
      read,
      async (milliseconds) => { delays.push(milliseconds); },
      controller.signal,
    );

    expect(delays).toEqual([500, 1_000, 2_000]);
    expect(read).toHaveBeenCalledTimes(3);
    expect(result.status).toBe("terminal");
  });

  it("stops after 60 reads without retrying a mutation", async () => {
    const read = vi.fn().mockResolvedValue(runningJob("running"));
    const result = await pollResearchJob(
      { version: 1, session_id: sessionId, job_id: "job_discovery_1", kind: "research_discovery" },
      read,
      vi.fn().mockResolvedValue(undefined),
      new AbortController().signal,
    );

    expect(read).toHaveBeenCalledTimes(60);
    expect(result).toEqual({ status: "paused", reason: "limit" });
  });

  it("stops before GET when the page is hidden or the attempt is aborted", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    const hiddenRead = vi.fn();
    const attempt = { version: 1, session_id: sessionId, job_id: "job_discovery_1", kind: "research_discovery" } as const;
    await expect(
      pollResearchJob(attempt, hiddenRead, vi.fn(), new AbortController().signal),
    ).resolves.toEqual({ status: "paused", reason: "hidden" });
    expect(hiddenRead).not.toHaveBeenCalled();

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    const aborted = new AbortController();
    aborted.abort();
    const abortedRead = vi.fn();
    await expect(
      pollResearchJob(attempt, abortedRead, vi.fn(), aborted.signal),
    ).resolves.toEqual({ status: "paused", reason: "aborted" });
    expect(abortedRead).not.toHaveBeenCalled();
  });
});

describe("research creation", () => {
  it("bounds and normalizes fields, persists identity before POST, then navigates to the validated session", async () => {
    const root = renderNewDom();
    root.querySelector<HTMLInputElement>("[name=topic]")!.value = " Local inference ";
    root.querySelector<HTMLTextAreaElement>("[name=queries]")!.value =
      "mlx inference\nollama performance";
    root.querySelector<HTMLInputElement>("[name=languages]")!.value = "EN, fr";
    root.querySelector<HTMLSelectElement>("[name=freshness_profile]")!.value = "standard";
    const navigate = vi.fn();
    const write = vi.fn((_path: string, body: unknown) => {
      expect(window.sessionStorage.getItem("yt-insights:research-start:v1")).toContain(
        (body as { idempotency_key: string }).idempotency_key,
      );
      return Promise.resolve(session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh"));
    });
    attachResearchNewPage(root, {
      write,
      navigate,
      createId: () => "123e4567-e89b-42d3-a456-426614174000",
    });

    submit(root.querySelector("form")!);

    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("/research/research_session_1/"));
    expect(write).toHaveBeenCalledWith(
      "/api/v1/research/sessions",
      {
        topic: "Local inference",
        queries: ["mlx inference", "ollama performance"],
        languages: ["en", "fr"],
        freshness_profile: "standard",
        idempotency_key: "123e4567-e89b-42d3-a456-426614174000",
      },
      expect.any(AbortSignal),
    );
    expect(window.sessionStorage.getItem("yt-insights:research-start:v1")).toBeNull();
  });

  it("never silently retries a lost creation response and exposes an explicit same-key retry", async () => {
    const root = renderNewDom();
    root.querySelector<HTMLInputElement>("[name=topic]")!.value = "Local inference";
    root.querySelector<HTMLTextAreaElement>("[name=queries]")!.value = "mlx inference";
    root.querySelector<HTMLInputElement>("[name=languages]")!.value = "en";
    const write = vi
      .fn()
      .mockRejectedValueOnce({ code: "unexpected_response" })
      .mockResolvedValueOnce(session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh"));
    attachResearchNewPage(root, {
      write,
      navigate: vi.fn(),
      createId: () => "123e4567-e89b-42d3-a456-426614174000",
    });

    submit(root.querySelector("form")!);
    await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>("[data-research-new-retry]")?.hidden).toBe(false));
    expect(write).toHaveBeenCalledTimes(1);

    root.querySelector<HTMLButtonElement>("[data-research-new-retry]")!.click();
    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    expect((write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key).toBe(
      (write.mock.calls[1]?.[1] as { idempotency_key: string }).idempotency_key,
    );
  });

  it("accepts 500-code-point language tags and rejects 501 before POST", async () => {
    const validRoot = renderNewDom();
    validRoot.querySelector<HTMLInputElement>("[name=topic]")!.value = "Language bounds";
    validRoot.querySelector<HTMLTextAreaElement>("[name=queries]")!.value = "local models";
    validRoot.querySelector<HTMLInputElement>("[name=languages]")!.value = "a".repeat(500);
    const validWrite = vi.fn().mockResolvedValue(
      session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh"),
    );
    attachResearchNewPage(validRoot, { write: validWrite, navigate: vi.fn() });

    submit(validRoot.querySelector("form")!);
    await vi.waitFor(() => expect(validWrite).toHaveBeenCalledOnce());

    document.body.replaceChildren();
    const invalidRoot = renderNewDom();
    invalidRoot.querySelector<HTMLInputElement>("[name=topic]")!.value = "Language bounds";
    invalidRoot.querySelector<HTMLTextAreaElement>("[name=queries]")!.value = "local models";
    invalidRoot.querySelector<HTMLInputElement>("[name=languages]")!.value = "a".repeat(501);
    const invalidWrite = vi.fn();
    attachResearchNewPage(invalidRoot, { write: invalidWrite });

    submit(invalidRoot.querySelector("form")!);
    expect(invalidWrite).not.toHaveBeenCalled();
    expect(invalidRoot.querySelector("[data-research-new-status]")?.textContent).toContain(
      "500 code points",
    );
  });
});

describe("cumulative research workspace", () => {
  it("keeps the mobile DOM sequence evidence, controls, candidates, history, timeline", () => {
    const root = renderWorkspaceDom();
    const directOrder = [
      "[data-evidence-panel]",
      "[data-research-controls]",
      "[data-candidate-list]",
      "[data-acquisition-history]",
      "[data-event-timeline]",
    ].map((selector) => [...root.children].indexOf(root.querySelector(selector)!));

    expect(directOrder).toEqual([...directOrder].sort((left, right) => left - right));
    const controls = root.querySelector<HTMLElement>("[data-research-controls]")!;
    expect([...controls.children]).toEqual([
      controls.querySelector("[data-decision-panel]"),
      controls.querySelector("[data-job-progress]"),
    ]);
  });

  it("renders evidence as text and exactly two primary sufficiency choices", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh")),
      write: vi.fn(),
      wait: vi.fn(),
    });

    await vi.waitFor(() => expect(root.querySelectorAll("[data-primary-choice]")).toHaveLength(2));
    expect(root.querySelector("[data-evidence-panel]")?.textContent).toContain(
      "<img src=x onerror=alert(1)> Local evidence.",
    );
    expect(root.querySelector("[data-evidence-panel] img")).toBeNull();
    expect(root.querySelector("[data-decision-panel]")?.textContent).toContain("Use current evidence");
    expect(root.querySelector("[data-decision-panel]")?.textContent).toContain("Search YouTube for more");
  });

  it("requires 1 to 5 exact candidates and guards duplicate approval submissions", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const approved = session("acquiring", null, 2);
    const write = vi.fn().mockResolvedValue(approved);
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("awaiting_candidate_approval", "approve_candidates_or_cancel")),
      write,
      wait: vi.fn(),
      createId: () => "123e4567-e89b-42d3-a456-426614174000",
    });
    await vi.waitFor(() => expect(root.querySelectorAll("[data-candidate-id]")).toHaveLength(1));
    const approve = root.querySelector<HTMLButtonElement>("[data-approve-candidates]")!;
    expect(approve.disabled).toBe(true);

    root.querySelector<HTMLInputElement>("[data-candidate-id]")!.click();
    expect(approve.disabled).toBe(false);
    approve.click();
    approve.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledOnce());
    expect(write).toHaveBeenCalledWith(
      `/api/v1/research/sessions/${sessionId}/approvals`,
      {
        expected_revision: 1,
        video_ids: ["xyz987QWE12"],
        idempotency_key: "123e4567-e89b-42d3-a456-426614174000",
      },
      expect.any(AbortSignal),
    );
  });

  it("offers an explicit confirmed cancellation and reloads stale cancellation state", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const current = session("awaiting_candidate_approval", "approve_candidates_or_cancel", 2);
    const read = vi.fn().mockResolvedValue(current);
    const write = vi.fn().mockRejectedValue({ code: "stale_revision", status: 409 });
    const confirm = vi.fn().mockReturnValue(true);
    Object.defineProperty(window, "confirm", { configurable: true, value: confirm });
    attachResearchWorkspace(root, {
      read,
      write,
      wait: vi.fn(),
      createId: () => "123e4567-e89b-42d3-a456-426614174000",
    });
    await vi.waitFor(() => expect(root.querySelector("[data-cancel-research]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-cancel-research]")!.click();

    await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(confirm).toHaveBeenCalledOnce();
    expect(write).toHaveBeenCalledWith(
      `/api/v1/research/sessions/${sessionId}/cancellations`,
      {
        expected_revision: 2,
        idempotency_key: "123e4567-e89b-42d3-a456-426614174000",
      },
      expect.any(AbortSignal),
    );
    expect(root.querySelector("[data-research-status]")?.textContent).toBe(
      "The session changed. Review the current evidence before deciding again.",
    );
  });

  it("reloads a stale snapshot and requires the decision again", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const current = session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh", 2);
    const read = vi.fn().mockResolvedValue(current);
    const write = vi.fn().mockRejectedValue({ code: "stale_revision", status: 409 });
    attachResearchWorkspace(root, {
      read,
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelectorAll("[data-primary-choice]")).toHaveLength(2));

    root.querySelector<HTMLButtonElement>("[data-decision=sufficient]")!.click();

    await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(root.querySelector("[data-research-status]")?.textContent).toBe(
      "The session changed. Review the current evidence before deciding again.",
    );
    expect(root.querySelectorAll("[data-primary-choice]")).toHaveLength(2);
  });

  it("persists and polls one discovery job without resubmitting or auto-retrying failure", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const discovering = session("discovering", null, 2);
    const read = vi
      .fn()
      .mockResolvedValueOnce(discovering)
      .mockResolvedValueOnce(job("research_discovery", "failed", null))
      .mockResolvedValueOnce(session("failed_retryable", null, 3));
    const write = vi.fn().mockResolvedValue({ schema_version: 1, job_id: "job_discovery_1" });
    attachResearchWorkspace(root, {
      read,
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();
    root.querySelector<HTMLButtonElement>("[data-start-discovery]")?.click();

    await vi.waitFor(() => expect(root.querySelector("[data-retry-research]")).not.toBeNull());
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0]?.[1]).toEqual({
      expected_revision: 2,
      idempotency_key: expect.stringMatching(/^web-research-[0-9a-f]{64}-0$/),
    });
    expect(root.querySelector("[data-job-message]")?.textContent).toContain("failed");
    expect(window.sessionStorage.getItem("yt-insights:research-job:v1")).toBeNull();
  });

  it("persists a job admission before POST and retries an ambiguous response only explicitly", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const read = vi
      .fn()
      .mockResolvedValueOnce(session("discovering", null, 2))
      .mockResolvedValueOnce(job("research_discovery", "failed", null))
      .mockResolvedValueOnce(session("failed_retryable", null, 3));
    const write = vi
      .fn((_path: string, body: unknown) => {
        expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toContain(
          (body as { idempotency_key: string }).idempotency_key,
        );
        return write.mock.calls.length === 1
          ? Promise.reject({ code: "unexpected_response" })
          : Promise.resolve({ schema_version: 1 as const, job_id: "job_discovery_1" });
      });
    attachResearchWorkspace(root, {
      read,
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );
    expect(write).toHaveBeenCalledTimes(1);

    root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!.click();
    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    expect(write.mock.calls[1]?.[1]).toEqual(write.mock.calls[0]?.[1]);
    await vi.waitFor(() => expect(root.querySelector("[data-retry-research]")).not.toBeNull());
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toBeNull();
  });

  it("rotates the shared identity only after an exact failed terminal job", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const coordinator = createAttemptIdentityCoordinator(
      window.localStorage,
      serialLock(),
      "research",
    );
    const firstFailed: JobResponse = {
      schema_version: 1,
      job: {
        job_id: "job_generation_0",
        kind: "research_discovery",
        status: "failed",
        result: null,
        error_code: "operation_failed",
      },
    };
    const read = vi
      .fn()
      .mockResolvedValueOnce(session("discovering", null, 2))
      .mockResolvedValueOnce(firstFailed)
      .mockResolvedValueOnce(session("discovering", null, 2));
    const write = vi
      .fn()
      .mockResolvedValueOnce({ schema_version: 1, job_id: "job_generation_0" })
      .mockResolvedValueOnce({ schema_version: 1, job_id: "job_generation_1" });
    attachResearchWorkspace(root, { read, write, wait: vi.fn(), coordinator });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    const firstKey = (write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key;
    const secondKey = (write.mock.calls[1]?.[1] as { idempotency_key: string }).idempotency_key;
    expect(firstKey).toMatch(/^web-research-[0-9a-f]{64}-0$/);
    expect(secondKey).toBe(firstKey.replace(/-0$/, "-1"));
  });

  it("preserves a v1 admitted key when reloading an ambiguous admission", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const legacyKey = `web:research_discovery:${sessionId}:2`;
    window.sessionStorage.setItem(
      "yt-insights:research-admission:v1",
      JSON.stringify({
        version: 1,
        session_id: sessionId,
        kind: "research_discovery",
        expected_revision: 2,
        idempotency_key: legacyKey,
        language: null,
      }),
    );
    const root = renderWorkspaceDom();
    const write = vi.fn().mockRejectedValue(new TypeError("response lost"));
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );

    root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledOnce());
    expect((write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key).toBe(legacyKey);
  });

  it("clears workflow conflicts, reloads the snapshot, and requires review", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const read = vi
      .fn()
      .mockResolvedValueOnce(session("discovering", null, 2))
      .mockResolvedValueOnce(session("awaiting_sufficiency_confirmation", "confirm_sufficiency_or_refresh", 3));
    const write = vi.fn().mockRejectedValue({ code: "workflow_conflict", status: 409 });
    attachResearchWorkspace(root, {
      read,
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toBeNull();
    expect(root.querySelector("[data-research-status]")?.textContent).toBe(
      "The research workflow changed. Review the current session before starting again.",
    );
    expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(true);
  });

  it("clears a missing session admission and unlocks the rendered controls", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const write = vi.fn().mockRejectedValue({ code: "not_found", status: 404 });
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-research-status]")?.textContent).toBe(
        "This research session was not found.",
      ),
    );
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(true);
    expect(root.querySelector<HTMLButtonElement>("[data-start-discovery]")?.disabled).toBe(false);
  });

  it.each([
    ["request in progress", { code: "request_in_progress", status: 409 }],
    ["rate limit", { code: "job_queue_full", status: 429 }],
    ["temporary outage", { code: "jobs_unavailable", status: 503 }],
    ["network loss", new TypeError("network lost")],
    ["ambiguous server failure", { code: "internal_error", status: 500 }],
  ])("retains the exact admission after %s", async (_case, failure) => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const write = vi.fn().mockRejectedValue(failure);
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).not.toBeNull();
    expect(write).toHaveBeenCalledOnce();
  });

  it.each([
    ["invalid request", { code: "invalid_request", status: 400 }],
    ["identity conflict", { code: "idempotency_conflict", status: 409 }],
    ["forbidden", { code: "forbidden", status: 403 }],
  ])("discards the admission after definitive %s", async (_case, failure) => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const write = vi.fn().mockRejectedValue(failure);
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-research-status]")?.textContent).not.toContain(
        "Submitting",
      ),
    );
    expect(write).toHaveBeenCalledOnce();
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toBeNull();
    expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(true);
    expect(root.querySelector<HTMLButtonElement>("[data-start-discovery]")?.disabled).toBe(false);
  });

  it("moves focus out of an admission panel hidden by a definitive failure", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    window.sessionStorage.setItem(
      "yt-insights:research-admission:v1",
      JSON.stringify({
        version: 1,
        session_id: sessionId,
        kind: "research_discovery",
        expected_revision: 2,
        idempotency_key: `web:research_discovery:${sessionId}:2`,
        language: null,
      }),
    );
    const root = renderWorkspaceDom();
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write: vi.fn().mockRejectedValue({ code: "forbidden", status: 403 }),
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );
    const retry = root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!;
    const status = root.querySelector<HTMLElement>("[data-research-status]")!;
    retry.focus();
    expect(document.activeElement).toBe(retry);

    retry.click();

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLElement>("[data-job-progress]")?.hidden).toBe(true),
    );
    expect(status.getAttribute("tabindex")).toBe("-1");
    expect(document.activeElement).toBe(status);
  });

  it("does not move focus when a definitive admission clear hides another panel", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    window.sessionStorage.setItem(
      "yt-insights:research-admission:v1",
      JSON.stringify({
        version: 1,
        session_id: sessionId,
        kind: "research_discovery",
        expected_revision: 2,
        idempotency_key: `web:research_discovery:${sessionId}:2`,
        language: null,
      }),
    );
    const root = renderWorkspaceDom();
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write: vi.fn().mockRejectedValue({ code: "forbidden", status: 403 }),
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );
    const retry = root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!;
    const heading = root.querySelector<HTMLElement>("[data-research-heading]")!;
    heading.tabIndex = -1;
    heading.focus();
    expect(document.activeElement).toBe(heading);

    retry.click();

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLElement>("[data-job-progress]")?.hidden).toBe(true),
    );
    expect(document.activeElement).toBe(heading);
  });

  it("keeps the admission and all controls locked when conflict retirement fails", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const scope = researchActionScope(
      sessionId,
      "research_discovery",
      2,
      null,
    );
    const fingerprint = await researchScopeFingerprint(scope);
    const baseCoordinator = createAttemptIdentityCoordinator(
      window.localStorage,
      serialLock(),
      "research",
    );
    const complete = vi.fn().mockRejectedValue({ code: "attempt_coordination_unavailable" });
    const root = renderWorkspaceDom();
    const write = vi.fn().mockRejectedValue({ code: "idempotency_conflict", status: 409 });
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator: { ...baseCoordinator, complete },
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-research-status]")?.textContent).toBe(
        "This conflicted request identity could not be retired. Research controls remain locked.",
      ),
    );
    const primary = root.querySelector<HTMLButtonElement>("[data-start-discovery]")!;
    const retry = root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!;
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toContain(
      `web-research-${fingerprint}-0`,
    );
    expect(primary.disabled).toBe(true);
    expect(retry.hidden).toBe(true);
    expect(retry.disabled).toBe(true);
    expect(complete).toHaveBeenCalledWith(fingerprint, 0);

    primary.click();
    retry.click();
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));

    expect(write).toHaveBeenCalledOnce();
    await expect(baseCoordinator.claim(fingerprint)).resolves.toBe(0);
  });

  it("poisons one conflicted generation, clears its panel, and waits for a new explicit click", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const scope = researchActionScope(
      sessionId,
      "research_discovery",
      2,
      null,
    );
    const fingerprint = await researchScopeFingerprint(scope);
    const firstKey = `web-research-${fingerprint}-0`;
    window.sessionStorage.setItem(
      "yt-insights:research-admission:v1",
      JSON.stringify({
        version: 2,
        session_id: sessionId,
        kind: "research_discovery",
        expected_revision: 2,
        idempotency_key: firstKey,
        language: null,
        scope_fingerprint: fingerprint,
        generation: 0,
      }),
    );
    const coordinator = createAttemptIdentityCoordinator(
      window.localStorage,
      serialLock(),
      "research",
    );
    await coordinator.claim(fingerprint);
    const root = renderWorkspaceDom();
    const write = vi
      .fn()
      .mockRejectedValueOnce({ code: "idempotency_conflict", status: 409 })
      .mockRejectedValueOnce(new TypeError("response lost"));
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator,
    });
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );

    root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")!.click();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-research-status]")?.textContent).toContain(
        "conflicts with another decision",
      ),
    );
    expect(write).toHaveBeenCalledTimes(1);
    expect((write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key).toBe(firstKey);
    expect(root.querySelector<HTMLElement>("[data-job-progress]")?.hidden).toBe(true);
    expect(root.querySelector("[data-job-message]")?.textContent).toBe("");
    expect(root.querySelector("[data-job-id]")?.textContent).toBe("");
    expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(true);
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toBeNull();
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
    expect(write).toHaveBeenCalledTimes(1);

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    expect((write.mock.calls[1]?.[1] as { idempotency_key: string }).idempotency_key).toBe(
      `web-research-${fingerprint}-1`,
    );
  });

  it("bounds a pending admission to 15 seconds and retains the exact key", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const root = renderWorkspaceDom();
    const write = vi.fn().mockReturnValue(new Promise(() => undefined));
    const coordinator = createAttemptIdentityCoordinator(
      window.localStorage,
      serialLock(),
      "research",
    );
    attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn(),
      coordinator,
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());
    vi.useFakeTimers();

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(write).toHaveBeenCalledOnce();
    const admittedKey = (write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key;

    await vi.advanceTimersByTimeAsync(15_000);

    expect(root.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false);
    expect(window.sessionStorage.getItem("yt-insights:research-admission:v1")).toContain(admittedKey);
    const fingerprint = admittedKey.slice("web-research-".length, -2);
    await expect(coordinator.claim(fingerprint)).resolves.toBe(0);
  });

  it("retains the accepted admission when the job identity cannot be persisted", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    const originalSetItem = window.sessionStorage.setItem.bind(window.sessionStorage);
    const setItemSpy = vi.spyOn(window.sessionStorage, "setItem").mockImplementation(function (
      key: string,
      value: string,
    ): void {
      if (key === "yt-insights:research-job:v1") throw new DOMException("Storage full");
      originalSetItem(key, value);
    });
    const root = renderWorkspaceDom();
    const write = vi.fn().mockResolvedValue({ schema_version: 1, job_id: "job_discovery_1" });
    const dispose = attachResearchWorkspace(root, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write,
      wait: vi.fn().mockResolvedValue(undefined),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });
    await vi.waitFor(() => expect(root.querySelector("[data-start-discovery]")).not.toBeNull());
    Object.defineProperty(document, "hidden", { configurable: true, value: true });

    root.querySelector<HTMLButtonElement>("[data-start-discovery]")!.click();

    await vi.waitFor(() => expect(write).toHaveBeenCalledOnce());
    const admittedKey = (write.mock.calls[0]?.[1] as { idempotency_key: string }).idempotency_key;
    const storedJob = window.sessionStorage.getItem("yt-insights:research-job:v1");
    const storedAdmission = window.sessionStorage.getItem("yt-insights:research-admission:v1");
    setItemSpy.mockRestore();
    expect(storedJob).toBeNull();
    expect(storedAdmission).toContain(admittedKey);
    dispose();

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.body.replaceChildren();
    const reloadedRoot = renderWorkspaceDom();
    const replayWrite = vi.fn();
    attachResearchWorkspace(reloadedRoot, {
      read: vi.fn().mockResolvedValue(session("discovering", null, 2)),
      write: replayWrite,
      wait: vi.fn(),
    });

    await vi.waitFor(() =>
      expect(reloadedRoot.querySelector<HTMLButtonElement>("[data-job-retry-admission]")?.hidden).toBe(false),
    );
    expect(replayWrite).not.toHaveBeenCalled();
  });

  it("treats an evicted persisted job as terminal and reloads the durable session", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    window.sessionStorage.setItem(
      "yt-insights:research-job:v1",
      JSON.stringify({ version: 1, session_id: sessionId, job_id: "evicted_job", kind: "research_discovery" }),
    );
    const root = renderWorkspaceDom();
    const read = vi
      .fn()
      .mockRejectedValueOnce({ code: "not_found", status: 404 })
      .mockResolvedValueOnce(session("awaiting_candidate_approval", "approve_candidates_or_cancel", 3));

    attachResearchWorkspace(root, { read, write: vi.fn(), wait: vi.fn() });

    await vi.waitFor(() => expect(root.querySelector("[data-decision-panel]")?.textContent).toContain("Approve exact videos"));
    expect(window.sessionStorage.getItem("yt-insights:research-job:v1")).toBeNull();
  });

  it("rejects a persisted job whose generation scope does not match its action", async () => {
    window.history.replaceState({}, "", `/research/${sessionId}/`);
    window.sessionStorage.setItem(
      "yt-insights:research-job:v1",
      JSON.stringify({
        version: 2,
        session_id: sessionId,
        job_id: "forged_scope_job",
        kind: "research_discovery",
        expected_revision: 2,
        language: null,
        scope_fingerprint: "a".repeat(64),
        generation: 0,
        idempotency_key: `web-research-${"a".repeat(64)}-0`,
      }),
    );
    const root = renderWorkspaceDom();
    const read = vi.fn().mockResolvedValue(session("discovering", null, 2));

    attachResearchWorkspace(root, {
      read,
      write: vi.fn(),
      wait: vi.fn(),
      coordinator: createAttemptIdentityCoordinator(window.localStorage, serialLock(), "research"),
    });

    await vi.waitFor(() =>
      expect(root.querySelector("[data-research-status]")?.textContent).toBe(
        "The stored background job identity was invalid. The durable session was reloaded.",
      ),
    );
    expect(read).toHaveBeenCalledOnce();
    expect(read.mock.calls[0]?.[0]).toBe(`/api/v1/research/sessions/${sessionId}`);
    expect(window.sessionStorage.getItem("yt-insights:research-job:v1")).toBeNull();
  });
});

describe("exports", () => {
  it("opens only the opaque API URL projected by the server and reports partial inventory", async () => {
    const fixture: ExportsResponse = {
      schema_version: 1,
      items: [
        {
          name: "local-inference/2026-08-31-research",
          session_id: sessionId,
          created_at: "2026-08-31T11:00:00Z",
          manifest_valid: true,
          export_id: "f".repeat(64),
          open_url: `/api/v1/exports/${"f".repeat(64)}/dossier`,
        },
      ],
      limit: 20,
      truncated: true,
      inventory_complete: false,
      inventory_examined: 32,
      inventory_limit: 32,
    };
    const root = renderExportsDom();
    attachExportsPage(root, vi.fn().mockResolvedValue(fixture));

    await vi.waitFor(() => expect(root.querySelector("[data-export-open]")).not.toBeNull());
    const link = root.querySelector<HTMLAnchorElement>("[data-export-open]")!;
    expect(link.getAttribute("href")).toBe(fixture.items[0]?.open_url);
    expect(link.target).toBe("_blank");
    expect(link.rel).toBe("noopener noreferrer");
    expect(root.textContent).not.toContain("/Users/");
    expect(root.querySelector("[data-exports-status]")?.textContent).toContain("partial inventory");
  });
});

function job(
  kind: "research_discovery" | "research_acquisition" | "research_retry",
  status: "failed" | "succeeded",
  result: ResearchResponse | null,
): JobResponse {
  return {
    schema_version: 1,
    job:
      status === "failed"
        ? { job_id: "job_discovery_1", kind, status, result: null, error_code: "operation_failed" }
        : { job_id: "job_discovery_1", kind, status, result: result!, error_code: null },
  };
}

function runningJob(status: "queued" | "running"): JobResponse {
  return {
    schema_version: 1,
    job: {
      job_id: "job_discovery_1",
      kind: "research_discovery",
      status,
      result: null,
      error_code: null,
    },
  };
}
