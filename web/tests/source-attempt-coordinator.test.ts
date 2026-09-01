import { afterEach, describe, expect, it } from "vitest";

import {
  acquisitionAttemptKey,
  createAttemptIdentityCoordinator,
} from "../src/lib/source-attempt-coordinator";

const FINGERPRINT = "a".repeat(64);

function serialLock(): <T>(name: string, task: () => T | Promise<T>) => Promise<T> {
  let tail = Promise.resolve();
  return async <T>(_name: string, task: () => T | Promise<T>): Promise<T> => {
    const previous = tail;
    let release = (): void => undefined;
    tail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      return await task();
    } finally {
      release();
    }
  };
}

afterEach(() => window.localStorage.clear());

describe("source attempt identity coordinator", () => {
  it("converges concurrent tabs on one identity for the same plan", async () => {
    const withLock = serialLock();
    const firstTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const secondTab = createAttemptIdentityCoordinator(window.localStorage, withLock);

    const [first, second] = await Promise.all([
      firstTab.claim(FINGERPRINT),
      secondTab.claim(FINGERPRINT),
    ]);

    expect(first).toBe(0);
    expect(second).toBe(0);
    expect(acquisitionAttemptKey(FINGERPRINT, first)).toBe(
      acquisitionAttemptKey(FINGERPRINT, second),
    );
    expect(acquisitionAttemptKey(FINGERPRINT, 999_999_999)).toHaveLength(93);
  });

  it("keeps a lost-response identity current until a terminal observer rotates it", async () => {
    const withLock = serialLock();
    const firstTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const reloadedTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const generation = await firstTab.claim(FINGERPRINT);

    await expect(reloadedTab.isCurrent(FINGERPRINT, generation)).resolves.toBe(true);
    await expect(reloadedTab.claim(FINGERPRINT)).resolves.toBe(generation);

    await reloadedTab.complete(FINGERPRINT, generation);
    await expect(firstTab.isCurrent(FINGERPRINT, generation)).resolves.toBe(false);
    await expect(firstTab.claim(FINGERPRINT)).resolves.toBe(generation + 1);
  });

  it("advances monotonically when terminal observers interleave", async () => {
    const withLock = serialLock();
    const firstTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const secondTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const generation = await firstTab.claim(FINGERPRINT);

    const [first, second] = await Promise.all([
      firstTab.complete(FINGERPRINT, generation),
      secondTab.complete(FINGERPRINT, generation),
    ]);

    expect(first).toBe(generation + 1);
    expect(second).toBe(generation + 1);
    await expect(firstTab.claim(FINGERPRINT)).resolves.toBe(generation + 1);
  });

  it("keeps admission validation and POST under one lock, then rejects a stale POST", async () => {
    const withLock = serialLock();
    const admittingTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const terminalTab = createAttemptIdentityCoordinator(window.localStorage, withLock);
    const generation = await admittingTab.claim(FINGERPRINT);
    let releasePost = (): void => undefined;
    const postResponse = new Promise<{ readonly job_id: string }>((resolve) => {
      releasePost = () => resolve({ job_id: "acquire-accepted" });
    });
    let markPostStarted = (): void => undefined;
    const postStarted = new Promise<void>((resolve) => {
      markPostStarted = resolve;
    });

    const admission = admittingTab.admit(
      FINGERPRINT,
      generation,
      async (claimedGeneration) => {
        expect(claimedGeneration).toBe(generation);
        markPostStarted();
        return postResponse;
      },
    );
    await postStarted;
    let terminalSettled = false;
    const terminal = terminalTab.complete(FINGERPRINT, generation).then((next) => {
      terminalSettled = true;
      return next;
    });
    await Promise.resolve();

    expect(terminalSettled).toBe(false);
    releasePost();
    await expect(admission).resolves.toEqual({
      status: "admitted",
      generation,
      value: { job_id: "acquire-accepted" },
    });
    await expect(terminal).resolves.toBe(generation + 1);

    let stalePostCalled = false;
    await expect(
      admittingTab.admit(FINGERPRINT, generation, () => {
        stalePostCalled = true;
        return Promise.resolve({ job_id: "must-not-post" });
      }),
    ).resolves.toEqual({ status: "stale", generation: generation + 1 });
    expect(stalePostCalled).toBe(false);
  });

  it("fails closed when shared coordination state is corrupt", async () => {
    window.localStorage.setItem(
      `yt-insights:source-attempt-generation:${FINGERPRINT}`,
      '{"version":1,"fingerprint":"wrong","generation":0}',
    );
    const coordinator = createAttemptIdentityCoordinator(
      window.localStorage,
      serialLock(),
    );

    await expect(coordinator.claim(FINGERPRINT)).rejects.toMatchObject({
      code: "attempt_coordination_unavailable",
    });
  });
});
