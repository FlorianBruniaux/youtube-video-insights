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
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("heading", { name: "1 passage found" })).toBeVisible();
  await expect(page.locator("[data-search-summary]")).toBeFocused();

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
  expect(decision?.body).toMatchObject({ expected_revision: 1, decision: "refresh" });
  expect(record(decision?.body).idempotency_key).toEqual(expect.any(String));
  expect(discovery?.body).toMatchObject({ expected_revision: 2 });
  expect(approval?.body).toMatchObject({ expected_revision: 2, video_ids: [PREVIEW_IDS[1]] });
});

test("cancels only after confirmation and never repeats the mutation", async ({ page }) => {
  if (fixture === undefined) throw new Error("Fixture server is unavailable");
  fixture.resetResearch("approval");
  await page.goto(`${fixture.origin}/research/${SESSION_ID}`);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Cancel research" }).click();
  await expect(page.getByRole("heading", { name: "No decision required" })).toBeVisible();
  await page.waitForTimeout(1_000);
  expect(fixture.mutations.filter((item) => item.path.endsWith("/cancellations"))).toHaveLength(1);
});

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}
