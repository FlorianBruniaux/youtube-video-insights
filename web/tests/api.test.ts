import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import backendSourceAcquisition from "./fixtures/backend-source-acquisition.json";

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

function cancellableResponse(headers: HeadersInit): {
  readonly response: Response;
  readonly cancelSpy: ReturnType<typeof vi.spyOn>;
} {
  const response = new Response(new ReadableStream<Uint8Array>(), {
    status: 200,
    headers,
  });
  if (response.body === null) throw new Error("test response body is missing");
  return {
    response,
    cancelSpy: vi.spyOn(response.body, "cancel").mockResolvedValue(undefined),
  };
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

  it("accepts the strict cancellation route and parses its research snapshot", async () => {
    const { history: _history, ...fixture } = researchFixture();
    const fetchSpy = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, mutation_token: "c".repeat(43) }),
      )
      .mockResolvedValueOnce(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchSpy);
    const { apiPost } = await import("../src/lib/api");
    const body = {
      expected_revision: 2,
      idempotency_key: "cancel-session-2",
    };

    await expect(
      apiPost("/api/v1/research/sessions/session_1/cancellations", body),
    ).resolves.toEqual(fixture);
    expect(fetchSpy.mock.calls[1]?.[0]).toBe(
      "/api/v1/research/sessions/session_1/cancellations",
    );
  });

  it("preserves AbortError instead of hiding cancellation behind a public error", async () => {
    const abort = new DOMException("cancelled by test", "AbortError");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(abort));
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toBe(abort);
  });
});

describe("strict response validation", () => {
  it("accepts the source acquisition fixture emitted by the current Python projection", async () => {
    const fixture = succeededJob(
      "source_acquisition",
      backendSourceAcquisition,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/jobs/job-contract-1")).resolves.toEqual(
      fixture,
    );
  });

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
      vi.fn<typeof fetch>().mockResolvedValue(
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

describe("adversarial response boundaries", () => {
  it.each(["get", "bootstrap", "post"] as const)(
    "preserves AbortError raised while decoding a %s response",
    async (stage) => {
      const abort = new DOMException("cancelled while reading", "AbortError");
      const abortedResponse = new Response(null, {
        status: stage === "post" ? 202 : 200,
        headers: { "Content-Type": "application/json" },
      });
      vi.spyOn(abortedResponse, "json").mockRejectedValue(abort);
      const fetchSpy = vi.fn<typeof fetch>();
      if (stage === "post") {
        fetchSpy
          .mockResolvedValueOnce(
            jsonResponse({ schema_version: 1, mutation_token: "t".repeat(43) }),
          )
          .mockResolvedValueOnce(abortedResponse);
      } else {
        fetchSpy.mockResolvedValue(abortedResponse);
      }
      vi.stubGlobal("fetch", fetchSpy);
      const { apiGet, apiPost } = await import("../src/lib/api");

      const operation =
        stage === "get"
          ? apiGet("/api/v1/status")
          : apiPost("/api/v1/sources/preview", {});

      await expect(operation).rejects.toBe(abort);
    },
  );

  it("rejects an oversized Content-Length before JSON materialization", async () => {
    const response = new Response(JSON.stringify(STATUS_FIXTURE), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": "4194305",
      },
    });
    const jsonSpy = vi.spyOn(response, "json");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code: "unexpected_response",
      status: 200,
    });
    expect(jsonSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["non-JSON", { "Content-Type": "text/plain" }],
    [
      "malformed Content-Length",
      { "Content-Type": "application/json", "Content-Length": "unknown" },
    ],
    [
      "duplicate Content-Length",
      { "Content-Type": "application/json", "Content-Length": "2, 3" },
    ],
    [
      "invalid Content-Length",
      { "Content-Type": "application/json", "Content-Length": "-1" },
    ],
    [
      "oversized Content-Length",
      { "Content-Type": "application/json", "Content-Length": "4194305" },
    ],
  ] as const)(
    "cancels the response body after a %s header rejection",
    async (_, headers) => {
      const { response, cancelSpy } = cancellableResponse(headers);
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
      const { apiGet } = await import("../src/lib/api");

      await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
        code: "unexpected_response",
        status: 200,
      });
      expect(cancelSpy).toHaveBeenCalledOnce();
    },
  );

  it("keeps the header validation error authoritative when body cancellation aborts", async () => {
    const abort = new DOMException("cancel failed", "AbortError");
    const { response, cancelSpy } = cancellableResponse({
      "Content-Type": "text/plain",
    });
    cancelSpy.mockRejectedValue(abort);
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    const { apiGet, PublicApiError } = await import("../src/lib/api");

    const error = await apiGet("/api/v1/status").catch(
      (value: unknown) => value,
    );

    expect(error).toBeInstanceOf(PublicApiError);
    expect(error).toMatchObject({ code: "unexpected_response", status: 200 });
    expect(error).not.toBe(abort);
  });

  it("rejects an oversized streamed body even without Content-Length", async () => {
    const oversized = `{"padding":"${"x".repeat(4_194_305)}"}`;
    const response = new Response(oversized, {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const jsonSpy = vi.spyOn(response, "json");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code: "unexpected_response",
      status: 200,
    });
    expect(jsonSpy).not.toHaveBeenCalled();
  });

  it("rejects integers that JavaScript cannot represent safely", async () => {
    const fixture = {
      ...STATUS_FIXTURE,
      corpus: { ...STATUS_FIXTURE.corpus, videos: Number.MAX_SAFE_INTEGER + 1 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/status")).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it("rejects source strings and arrays beyond backend projection limits", async () => {
    const fixture = sourceResponseFixture({
      title: "t".repeat(1_001),
      languages: Array.from({ length: 21 }, (_, index) => `l${index}`),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(
      apiGet("/api/v1/sources?limit=20&offset=0"),
    ).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it.each([
    ["preview title", 300, "accept"],
    ["preview title", 301, "reject"],
    ["research topic", 500, "accept"],
    ["research topic", 501, "reject"],
    ["research language", 500, "accept"],
    ["research language", 501, "reject"],
  ] as const)(
    "counts astral code points at the %s boundary (%i, %s)",
    async (field, length, expected) => {
      const astralValue = "\u{1F9EA}".repeat(length);
      const fixture =
        field === "preview title"
          ? succeededJob("source_preview", {
              ...sourcePreviewResult(),
              videos: [
                {
                  video_id: "abc123DEF45",
                  title: astralValue,
                  published_at: "2026-08-20",
                  url: "https://www.youtube.com/watch?v=abc123DEF45",
                },
              ],
            })
          : {
              ...researchFixture(),
              session: {
                ...researchFixture().session,
                ...(field === "research topic"
                  ? { topic: astralValue }
                  : { languages: [astralValue] }),
              },
            };
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
      );
      const { apiGet } = await import("../src/lib/api");
      const operation =
        field === "preview title"
          ? apiGet("/api/v1/jobs/job-contract-1")
          : apiGet("/api/v1/research/sessions/session_1");

      if (expected === "accept") {
        await expect(operation).resolves.toEqual(fixture);
      } else {
        await expect(operation).rejects.toMatchObject({
          code: "unexpected_response",
          status: 200,
        });
      }
    },
  );

  it("rejects an unknown source kind in a completed preview job", async () => {
    const fixture = succeededJob("source_preview", {
      ...sourcePreviewResult(),
      source_kind: "remote_shell",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/jobs/job-contract-1")).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it.each([{ videos_truncated: true }, { language: "../private" }])(
    "rejects an inconsistent source preview result %#",
    async (change) => {
      const fixture = succeededJob("source_preview", {
        ...sourcePreviewResult(),
        ...change,
      });
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
      );
      const { apiGet } = await import("../src/lib/api");

      await expect(apiGet("/api/v1/jobs/job-contract-1")).rejects.toMatchObject(
        {
          code: "unexpected_response",
        },
      );
    },
  );

  it.each([
    { items: [{ ...acquisitionItem(), status: "compromised" }] },
    { items: [{ ...acquisitionItem(), error_code: "download_failed" }] },
    { selected: 2 },
    { transcripts_ready: 2 },
    { insights_ready: 2 },
    { failure_count: 1 },
    { exit_code: 4 },
  ])("rejects an inconsistent source acquisition result %#", async (change) => {
    const fixture = succeededJob("source_acquisition", {
      ...sourceAcquisitionResult(),
      ...change,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture)),
    );
    const { apiGet } = await import("../src/lib/api");

    await expect(apiGet("/api/v1/jobs/job-contract-1")).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it.each([
    [409, "invalid_request"],
    [400, "stale_revision"],
    [503, "forbidden"],
    [429, "server_busy"],
  ] as const)(
    "rejects incompatible HTTP status %i for error code %s",
    async (status, code) => {
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
        code: "unexpected_response",
        status,
      });
    },
  );

  it("clears a rejected mutation token and bootstraps only on the next explicit POST", async () => {
    const firstToken = "a".repeat(43);
    const secondToken = "b".repeat(43);
    const fetchSpy = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, mutation_token: firstToken }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, error: { code: "forbidden" } }, 403),
      )
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: 1, mutation_token: secondToken }),
      )
      .mockResolvedValueOnce(jsonResponse(SOURCE_PREVIEW_FIXTURE, 202));
    vi.stubGlobal("fetch", fetchSpy);
    const { apiPost } = await import("../src/lib/api");

    await expect(apiPost("/api/v1/sources/preview", {})).rejects.toMatchObject({
      code: "forbidden",
      status: 403,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);

    await expect(apiPost("/api/v1/sources/preview", {})).resolves.toEqual(
      SOURCE_PREVIEW_FIXTURE,
    );
    expect(fetchSpy).toHaveBeenCalledTimes(4);
    expect(fetchSpy.mock.calls[1]?.[1]?.headers).toEqual({
      "Content-Type": "application/json",
      "X-YT-Insights-Token": firstToken,
    });
    expect(fetchSpy.mock.calls[3]?.[1]?.headers).toEqual({
      "Content-Type": "application/json",
      "X-YT-Insights-Token": secondToken,
    });
  });
});

function sourceResponseFixture(
  change: Partial<{
    title: string;
    languages: readonly string[];
  }> = {},
): object {
  return {
    schema_version: 1,
    items: [
      {
        video_id: "abc123DEF45",
        title: change.title ?? "Local model",
        published_at: "2026-08-20",
        languages: change.languages ?? ["en", "fr"],
        sources: ["example"],
        url: "https://www.youtube.com/watch?v=abc123DEF45",
        artifact_count: 2,
        transcript_state: "available",
        index_state: "indexed",
      },
    ],
    limit: 20,
    offset: 0,
  };
}

function sourcePreviewResult(): object {
  return {
    fingerprint: "a".repeat(64),
    source_kind: "channel",
    selected_count: 1,
    video_ids: ["abc123DEF45"],
    videos: [
      {
        video_id: "abc123DEF45",
        title: "Local model",
        published_at: "2026-08-20",
        url: "https://www.youtube.com/watch?v=abc123DEF45",
      },
    ],
    videos_returned: 1,
    videos_truncated: false,
    language: "fr",
    analyze: false,
    requires_confirmation: true,
    excluded_count: 0,
    discovery_error_count: 0,
  };
}

function acquisitionItem(): object {
  return {
    video_id: "abc123DEF45",
    status: "acquired",
    error_code: null,
    source_sha256: "b".repeat(64),
  };
}

function sourceAcquisitionResult(): object {
  return {
    selected: 1,
    transcripts_ready: 1,
    insights_ready: 0,
    failure_count: 0,
    exclusion_count: 0,
    items: [acquisitionItem()],
    exit_code: 0,
  };
}

function succeededJob(kind: string, result: object): object {
  return {
    schema_version: 1,
    job: {
      job_id: "job-contract-1",
      kind,
      status: "succeeded",
      result,
      error_code: null,
    },
  };
}

function researchFixture() {
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
      snapshot: {
        search_generation: "d".repeat(64),
        catalog_generation: "e".repeat(64),
      },
      coverage: {
        matched_passages: 1,
        matched_videos: 1,
        distinct_channels: 1,
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
          passage_id: "c".repeat(64),
          video_id: "abc123DEF45",
          channel_id: `UC${"x".repeat(22)}`,
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
