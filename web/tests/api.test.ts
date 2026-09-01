import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STATUS_FIXTURE = {
  schema_version: 1,
  status: "ok",
  corpus: {
    health: "ready",
    videos: 12,
    transcripts: 10,
    documents_indexed: 10,
    passages_indexed: 42,
  },
} as const;

const SOURCE_PREVIEW_FIXTURE = {
  schema_version: 1,
  job_id: "job-preview-1",
} as const;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("safe API requests", () => {
  it("keeps a GET token-free and preserves same-origin credentials and abort", async () => {
    const controller = new AbortController();
    const fetchSpy = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(STATUS_FIXTURE));
    vi.stubGlobal("fetch", fetchSpy);
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status", controller.signal)).resolves.toEqual(
      STATUS_FIXTURE,
    );

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [path, init] = fetchSpy.mock.calls[0] ?? [];
    expect(path).toBe("/api/v1/status");
    expect(init).toEqual({
      credentials: "same-origin",
      method: "GET",
      signal: controller.signal,
    });
  });

  it("fetches bootstrap JSON before a POST and sends only the exact mutation headers", async () => {
    const fetchSpy = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, mutation_token: "t".repeat(43) }),
      )
      .mockResolvedValueOnce(jsonResponse(SOURCE_PREVIEW_FIXTURE, 202));
    vi.stubGlobal("fetch", fetchSpy);
    const { apiPost } = await import("../src/lib/api");
    const body = {
      source: "https://www.youtube.com/watch?v=abc123DEF45",
      language: "en",
      analyze: false,
    };

    await expect(apiPost("/api/v1/sources/preview", body)).resolves.toEqual(
      SOURCE_PREVIEW_FIXTURE,
    );

    expect(fetchSpy.mock.calls).toEqual([
      ["/api/v1/bootstrap", { credentials: "same-origin", method: "GET" }],
      [
        "/api/v1/sources/preview",
        {
          body: JSON.stringify(body),
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-YT-Insights-Token": "t".repeat(43),
          },
          method: "POST",
        },
      ],
    ]);
  });

  it("does not retain a failed bootstrap token request", async () => {
    const fetchSpy = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, mutation_token: "r".repeat(43) }),
      )
      .mockResolvedValueOnce(jsonResponse(SOURCE_PREVIEW_FIXTURE, 202));
    vi.stubGlobal("fetch", fetchSpy);
    const { apiPost } = await import("../src/lib/api");

    await expect(apiPost("/api/v1/sources/preview", {})).rejects.toBeInstanceOf(
      TypeError,
    );
    await expect(apiPost("/api/v1/sources/preview", {})).resolves.toEqual(
      SOURCE_PREVIEW_FIXTURE,
    );
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("preserves AbortError instead of hiding cancellation behind a public error", async () => {
    const abort = new DOMException("cancelled by test", "AbortError");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(abort));
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toBe(abort);
  });
});

describe("strict response validation", () => {
  it("maps non-JSON server output to a fixed error without exposing response text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response("secret at /Users/private/catalog.sqlite3", {
          status: 500,
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );
    const { apiGet, PublicApiError } = await import("../src/lib/api");

    const error = await apiGet("/api/v1/status").catch(
      (value: unknown) => value,
    );

    expect(error).toBeInstanceOf(PublicApiError);
    expect(error).toMatchObject({ code: "unexpected_response", status: 500 });
    expect(String(error)).not.toContain("secret");
    expect(JSON.stringify(error)).not.toContain("/Users/private");
  });

  it.each([
    "stale_revision",
    "workflow_conflict",
    "plan_changed",
    "idempotency_conflict",
    "request_in_progress",
  ] as const)("preserves the public 409 code %s", async (code) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({ schema_version: 1, error: { code } }, 409),
        ),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code,
      status: 409,
    });
  });

  it.each([
    [429, "job_queue_full"],
    [503, "server_busy"],
    [503, "search_unavailable"],
  ] as const)("preserves bounded HTTP %i error %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({ schema_version: 1, error: { code } }, status),
        ),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code,
      status,
    });
  });

  it("rejects a malformed successful payload instead of casting unknown JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({
            ...STATUS_FIXTURE,
            corpus: { health: "ready", videos: "12" },
          }),
        ),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code: "unexpected_response",
      status: 200,
    });
  });

  it("parses enriched source transcript and index state", async () => {
    const fixture = {
      schema_version: 1,
      items: [
        {
          video_id: "abc123DEF45",
          title: "Local model",
          published_at: "2026-08-20",
          languages: ["en", "fr"],
          sources: ["example"],
          url: "https://www.youtube.com/watch?v=abc123DEF45",
          artifact_count: 2,
          transcript_state: "available",
          index_state: "indexed",
        },
      ],
      limit: 20,
      offset: 0,
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/sources?limit=20&offset=0")).resolves.toEqual(
      fixture,
    );
  });

  it("parses bounded research history and required user action", async () => {
    const fixture = researchFixture();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(
      apiGet("/api/v1/research/sessions/session_1"),
    ).resolves.toEqual(fixture);
  });

  it("parses an opaque export URL without deriving a local path", async () => {
    const exportId = "a".repeat(64);
    const fixture = {
      schema_version: 1,
      items: [
        {
          name: "2026-08-31-session",
          session_id: "session_1",
          created_at: "2026-08-31T10:00:00+00:00",
          manifest_valid: true,
          export_id: exportId,
          open_url: `/api/v1/exports/${exportId}/dossier`,
        },
      ],
      limit: 10,
      truncated: false,
      inventory_complete: true,
      inventory_examined: 2,
      inventory_limit: 32,
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/exports?limit=10")).resolves.toEqual(fixture);
  });

  it("keeps a succeeded job error as a typed result instead of treating it as transport failure", async () => {
    const fixture = {
      schema_version: 1,
      job: {
        job_id: "job-preview-1",
        kind: "source_preview",
        status: "succeeded",
        result: { schema_version: 1, error: { code: "plan_too_large" } },
        error_code: null,
      },
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/jobs/job-preview-1")).resolves.toEqual(
      fixture,
    );
  });

  it("accepts the worker's bounded truncation marker as a terminal job result", async () => {
    const fixture = {
      schema_version: 1,
      job: {
        job_id: "job-preview-1",
        kind: "source_preview",
        status: "succeeded",
        result: { truncated: true },
        error_code: null,
      },
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/jobs/job-preview-1")).resolves.toEqual(
      fixture,
    );
  });
});

function researchFixture(): object {
  return {
    schema_version: 1,
    session: {
      session_id: "session_1",
      topic: "Local AI",
      queries: ["local inference"],
      languages: ["en"],
      freshness_profile: "standard",
      discovery_fingerprint: "f".repeat(64),
      state: "awaiting_sufficiency_confirmation",
      revision: 5,
      retry_target: null,
      created_at: "2026-08-31T10:00:00+00:00",
      updated_at: "2026-08-31T10:10:00+00:00",
    },
    assessment: {
      created_at: "2026-08-31T10:05:00+00:00",
      snapshot: { search_generation: "s1", catalog_generation: "c1" },
      coverage: {
        matched_passages: 3,
        matched_videos: 2,
        distinct_channels: 2,
        queries_with_zero_hits: [],
        newest_source_published_at: "2026-08-20",
        unknown_publication_date_count: 0,
      },
      freshness: {
        profile: "standard",
        maximum_age_days: 30,
        last_successful_discovery_at: null,
        stale: false,
        reason: "fresh_local_evidence",
      },
      passages: [
        {
          query: "local inference",
          passage_id: "p".repeat(64),
          video_id: "abc123DEF45",
          channel_id: "channel-1",
          rank: 1,
          url: "https://www.youtube.com/watch?v=abc123DEF45&t=12s",
          excerpt: "Local evidence",
          source_sha256: "a".repeat(64),
        },
      ],
      videos: [
        {
          query: "local inference",
          video_id: "abc123DEF45",
          source_keys: ["example"],
          title: "Local inference",
          published_at: "2026-08-20",
          rank: 1.5,
          watch_url: "https://www.youtube.com/watch?v=abc123DEF45",
        },
      ],
    },
    candidates: null,
    required_user_action: "confirm_sufficiency_or_refresh",
    error_code: null,
    acquisition_history: [
      {
        attempt_id: "attempt-1",
        status: "completed",
        items: [
          {
            video_id: "abc123DEF45",
            status: "acquired",
            error_code: null,
            source_sha256: "b".repeat(64),
          },
        ],
      },
    ],
    acquisition_history_truncated: false,
    history: {
      decisions: [{ action: "refresh", created_at: "2026-08-31T10:06:00Z" }],
      events: [
        {
          event_id: 1,
          from_state: null,
          to_state: "assessing",
          event_code: "session_created",
          created_at: "2026-08-31T10:00:00Z",
        },
      ],
      decisions_truncated: false,
      events_truncated: false,
    },
  };
}
