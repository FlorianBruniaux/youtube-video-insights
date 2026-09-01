const SHA256 = /^[0-9a-f]{64}$/;
const MAX_GENERATION = 999_999_999;
const MAX_RECORD_BYTES = 256;
const STORAGE_PREFIX = "yt-insights:source-attempt-generation:";
const LOCK_PREFIX = "yt-insights-source-attempt-generation-";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export type WithExclusiveLock = <T>(
  name: string,
  task: () => T | Promise<T>,
) => Promise<T>;

export interface AttemptIdentityCoordinator {
  claim(fingerprint: string): Promise<number>;
  admit<T>(
    fingerprint: string,
    expectedGeneration: number | null,
    task: (generation: number) => T | Promise<T>,
  ): Promise<AdmissionResult<T>>;
  isCurrent(fingerprint: string, generation: number): Promise<boolean>;
  complete(fingerprint: string, completedGeneration: number): Promise<number>;
}

export type AdmissionResult<T> =
  | {
      readonly status: "admitted";
      readonly generation: number;
      readonly value: T;
    }
  | {
      readonly status: "stale";
      readonly generation: number;
    };

type LockedAdmissionResult<T> = AdmissionResult<T> | {
  readonly status: "task_failed";
  readonly error: unknown;
};

interface GenerationRecord {
  readonly version: 1;
  readonly fingerprint: string;
  readonly generation: number;
}

export function acquisitionAttemptKey(
  fingerprint: string,
  generation: number,
): string {
  requireFingerprint(fingerprint);
  requireGeneration(generation);
  return `web-source-acquire-${fingerprint}-${generation}`;
}

export function createAttemptIdentityCoordinator(
  storage: StorageLike,
  withLock: WithExclusiveLock,
): AttemptIdentityCoordinator {
  return {
    claim: (fingerprint) => withGenerationLock(fingerprint, withLock, () => {
      const current = readRecord(storage, fingerprint);
      if (current !== null) return current.generation;
      writeRecord(storage, { version: 1, fingerprint, generation: 0 });
      return 0;
    }),
    admit: async (fingerprint, expectedGeneration, task) => {
      const result = await withGenerationLock(
        fingerprint,
        withLock,
        async (): Promise<LockedAdmissionResult<Awaited<ReturnType<typeof task>>>> => {
          if (expectedGeneration !== null) requireGeneration(expectedGeneration);
          const current = readRecord(storage, fingerprint);
          const generation = current?.generation ?? 0;
          if (current === null) {
            writeRecord(storage, { version: 1, fingerprint, generation });
          }
          if (
            expectedGeneration !== null &&
            generation !== expectedGeneration
          ) {
            return { status: "stale", generation };
          }
          try {
            return {
              status: "admitted",
              generation,
              value: await task(generation),
            };
          } catch (error: unknown) {
            return { status: "task_failed", error };
          }
        },
      );
      if (result.status === "task_failed") throw result.error;
      return result;
    },
    isCurrent: (fingerprint, generation) =>
      withGenerationLock(fingerprint, withLock, () => {
        requireGeneration(generation);
        const current = readRecord(storage, fingerprint);
        if (current === null) throw coordinationError();
        return current.generation === generation;
      }),
    complete: (fingerprint, completedGeneration) =>
      withGenerationLock(fingerprint, withLock, () => {
        requireGeneration(completedGeneration);
        if (completedGeneration >= MAX_GENERATION) throw coordinationError();
        const current = readRecord(storage, fingerprint);
        const next = Math.max(
          current?.generation ?? 0,
          completedGeneration + 1,
        );
        requireGeneration(next);
        writeRecord(storage, { version: 1, fingerprint, generation: next });
        return next;
      }),
  };
}

export function createBrowserAttemptIdentityCoordinator(): AttemptIdentityCoordinator {
  let storage: Storage;
  try {
    storage = window.localStorage;
  } catch {
    return unavailableCoordinator();
  }
  const withLock: WithExclusiveLock = async (name, task) => {
    const locks = globalThis.navigator?.locks;
    if (locks === undefined || typeof locks.request !== "function") {
      throw coordinationError();
    }
    try {
      return await locks.request(name, { mode: "exclusive" }, task);
    } catch (error: unknown) {
      if (isCoordinationError(error)) throw error;
      throw coordinationError();
    }
  };
  return createAttemptIdentityCoordinator(storage, withLock);
}

function withGenerationLock<T>(
  fingerprint: string,
  withLock: WithExclusiveLock,
  task: () => T | Promise<T>,
): Promise<T> {
  requireFingerprint(fingerprint);
  try {
    return withLock(`${LOCK_PREFIX}${fingerprint}`, task);
  } catch {
    return Promise.reject(coordinationError());
  }
}

function readRecord(
  storage: StorageLike,
  fingerprint: string,
): GenerationRecord | null {
  let raw: string | null;
  try {
    raw = storage.getItem(`${STORAGE_PREFIX}${fingerprint}`);
  } catch {
    throw coordinationError();
  }
  if (raw === null) return null;
  if (raw.length > MAX_RECORD_BYTES) throw coordinationError();
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    throw coordinationError();
  }
  if (!isRecord(value)) throw coordinationError();
  const keys = Object.keys(value).sort();
  const expected = ["fingerprint", "generation", "version"];
  if (
    keys.length !== expected.length ||
    keys.some((key, index) => key !== expected[index]) ||
    value.version !== 1 ||
    value.fingerprint !== fingerprint ||
    !isGeneration(value.generation)
  ) {
    throw coordinationError();
  }
  return {
    version: 1,
    fingerprint,
    generation: value.generation,
  };
}

function writeRecord(storage: StorageLike, record: GenerationRecord): void {
  try {
    storage.setItem(
      `${STORAGE_PREFIX}${record.fingerprint}`,
      JSON.stringify(record),
    );
  } catch {
    throw coordinationError();
  }
}

function requireFingerprint(value: string): void {
  if (!SHA256.test(value)) throw coordinationError();
}

function requireGeneration(value: number): void {
  if (!isGeneration(value)) throw coordinationError();
}

function isGeneration(value: unknown): value is number {
  return Number.isSafeInteger(value) &&
    typeof value === "number" &&
    value >= 0 &&
    value <= MAX_GENERATION;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function coordinationError(): { readonly code: "attempt_coordination_unavailable" } {
  return { code: "attempt_coordination_unavailable" };
}

function isCoordinationError(
  value: unknown,
): value is { readonly code: "attempt_coordination_unavailable" } {
  return isRecord(value) && value.code === "attempt_coordination_unavailable";
}

function unavailableCoordinator(): AttemptIdentityCoordinator {
  const unavailable = async (): Promise<never> => {
    throw coordinationError();
  };
  return {
    claim: unavailable,
    admit: unavailable,
    isCurrent: unavailable,
    complete: unavailable,
  };
}
