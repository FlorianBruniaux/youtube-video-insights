import { expect, test } from "@playwright/test";

import {
  PREVIEW_IDS,
  SESSION_ID,
  startFixtureServer,
  type FixtureServer,
} from "./fixture-server";

let fixture: FixtureServer | undefined;

test.beforeAll(async () => {
  fixture = await startFixtureServer();
});

test.afterAll(async () => {
  await fixture?.close();
});

test.beforeEach(() => {
  fixture?.resetResearch();
});

test("searches, persists theme, and confirms an exact source plan", async ({ page }) => {
  if (fixture === undefined) throw new Error("Fixture server is unavailable");
  await page.goto(fixture.origin);
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByRole("link", { name: "Search the corpus" }).click();
  await page.getByLabel("Search transcripts").fill("local inference");
  await page.getByLabel("Channel").fill("local-ai");
  await page.getByLabel("Language").fill("en");
  await page.getByLabel("Results").selectOption("20");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("heading", { name: "1 passage found" })).toBeVisible();
  await expect(page.locator("[data-search-summary]")).toBeFocused();
  expect(fixture.reads.find((item) => item.path === "/api/v1/search")).toEqual({
    path: "/api/v1/search",
    query: {
      channel: ["local-ai"],
      language: ["en"],
      limit: ["20"],
      q: ["local inference"],
    },
  });

  await page.goto(`${fixture.origin}/sources/`);
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/@local-ai");
  await page.getByRole("button", { name: "Preview source" }).click();
  const plan = page.getByRole("region", { name: "Review 2 selected videos" });
  await expect(plan.getByRole("heading", { name: "Review 2 selected videos" })).toBeVisible();
  for (const videoId of PREVIEW_IDS) {
    await expect(plan.getByText(videoId, { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "Acquire these videos" }).click();
  await expect(page.getByText("2 transcripts ready from 2 selected videos.")).toBeVisible();

  const preview = fixture.mutations.find((item) => item.path.endsWith("/preview"));
  const acquire = fixture.mutations.find((item) => item.path.endsWith("/acquire"));
  expect(preview).toMatchObject({
    body: { source: "https://www.youtube.com/@local-ai", language: "en", analyze: false },
    token: "fixture-mutation-token-0123456789abcdef",
  });
  expect(acquire?.body).toEqual({
    fingerprint: "a".repeat(64),
    idempotency_key: `web-source-acquire-${"a".repeat(64)}-0`,
  });
  expect(acquire?.token).toBe("fixture-mutation-token-0123456789abcdef");
});

test("keeps sufficiency and candidate approval as separate explicit gates", async ({ page }) => {
  if (fixture === undefined) throw new Error("Fixture server is unavailable");
  await page.goto(`${fixture.origin}/research/${SESSION_ID}`);
  await expect(page.getByRole("heading", { name: "Is the current evidence sufficient?" })).toBeVisible();
  await page.getByRole("button", { name: "Search YouTube for more" }).click();
  await expect(page.getByRole("heading", { name: "Candidate discovery is ready" })).toBeVisible();
  await page.getByRole("button", { name: "Start candidate discovery" }).click();
  await expect(page.getByRole("heading", { name: "Approve exact videos" })).toBeVisible();
  await page.getByLabel("Fresh local inference benchmark").check();
  await page.getByRole("button", { name: "Approve selected candidates" }).click();
  await expect(page.getByRole("heading", { name: "Approved candidates are ready" })).toBeVisible();

  const decision = fixture.mutations.find((item) => item.path.endsWith("/decisions"));
  const discovery = fixture.mutations.find((item) => item.path.endsWith("/discovery"));
  const approval = fixture.mutations.find((item) => item.path.endsWith("/approvals"));
  const decisionKey = record(decision?.body).idempotency_key;
  const discoveryKey = record(discovery?.body).idempotency_key;
  const approvalKey = record(approval?.body).idempotency_key;
  expect(decision).toEqual({
    path: `/api/v1/research/sessions/${SESSION_ID}/decisions`,
    body: {
      expected_revision: 1,
      decision: "refresh",
      idempotency_key: decisionKey,
    },
    token: "fixture-mutation-token-0123456789abcdef",
  });
  expect(decisionKey).toEqual(expect.stringMatching(UUID_V4));
  expect(discovery).toEqual({
    path: `/api/v1/research/sessions/${SESSION_ID}/discovery`,
    body: { expected_revision: 2, idempotency_key: discoveryKey },
    token: "fixture-mutation-token-0123456789abcdef",
  });
  expect(discoveryKey).toEqual(expect.stringMatching(RESEARCH_ADMISSION_KEY));
  expect(approval).toEqual({
    path: `/api/v1/research/sessions/${SESSION_ID}/approvals`,
    body: {
      expected_revision: 2,
      video_ids: [PREVIEW_IDS[1]],
      idempotency_key: approvalKey,
    },
    token: "fixture-mutation-token-0123456789abcdef",
  });
  expect(approvalKey).toEqual(expect.stringMatching(UUID_V4));
  expect(new Set([decisionKey, discoveryKey, approvalKey]).size).toBe(3);
});

test("cancels only after confirmation and never repeats the mutation", async ({ page }) => {
  if (fixture === undefined) throw new Error("Fixture server is unavailable");
  fixture.resetResearch("approval");
  await page.goto(`${fixture.origin}/research/${SESSION_ID}`);
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Cancel research" }).click();
  expect(
    fixture.mutations.filter((item) => item.path.endsWith("/cancellations")),
  ).toHaveLength(0);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Cancel research" }).click();
  await expect(page.getByRole("heading", { name: "No decision required" })).toBeVisible();
  await page.waitForTimeout(1_000);
  const cancellations = fixture.mutations.filter((item) => item.path.endsWith("/cancellations"));
  expect(cancellations).toHaveLength(1);
  const cancellationKey = record(cancellations[0]?.body).idempotency_key;
  expect(cancellations[0]).toEqual({
    path: `/api/v1/research/sessions/${SESSION_ID}/cancellations`,
    body: { expected_revision: 2, idempotency_key: cancellationKey },
    token: "fixture-mutation-token-0123456789abcdef",
  });
  expect(cancellationKey).toEqual(expect.stringMatching(UUID_V4));
});

test("supports keyboard navigation and the reduced-motion preference", async ({
  page,
}) => {
  if (fixture === undefined) throw new Error("Fixture server is unavailable");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(fixture.origin);

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "YT Insights overview" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Corpus", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(`${fixture.origin}/search/`);

  const motion = await page
    .getByRole("button", { name: "Use dark theme" })
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        animationDuration: style.animationDuration,
        animationIterationCount: style.animationIterationCount,
        transitionDuration: style.transitionDuration,
      };
    });
  expect(motion).toEqual({
    animationDuration: "1e-05s",
    animationIterationCount: "1",
    transitionDuration: "1e-05s",
  });
});

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RESEARCH_ADMISSION_KEY = /^web-research-[0-9a-f]{64}-0$/;

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}
