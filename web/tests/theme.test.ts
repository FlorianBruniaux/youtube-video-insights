import { beforeEach, describe, expect, it } from "vitest";

import { applyStoredTheme } from "../src/lib/theme";

function fakeStorage(value: string | null): Storage {
  return {
    getItem: (key: string) => (key === "yt-insights-theme" ? value : null),
    setItem: () => undefined,
    removeItem: () => undefined,
    clear: () => undefined,
    key: () => null,
    get length() {
      return value === null ? 0 : 1;
    },
  };
}

describe("applyStoredTheme", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("defaults to dark and restores only known values", () => {
    const root = document.documentElement;

    expect(applyStoredTheme(fakeStorage(null), root)).toBe("dark");
    expect(root.dataset.theme).toBe("dark");

    expect(applyStoredTheme(fakeStorage("dark"), root)).toBe("dark");
    expect(root.dataset.theme).toBe("dark");

    expect(applyStoredTheme(fakeStorage("light"), root)).toBe("light");
    expect(root.dataset.theme).toBe("light");

    expect(applyStoredTheme(fakeStorage("system"), root)).toBe("dark");
    expect(root.dataset.theme).toBe("dark");
  });
});
