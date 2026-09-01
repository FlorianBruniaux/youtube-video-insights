import { describe, expect, it } from "vitest";

import { isNavigationLinkCurrent } from "../src/lib/navigation";

describe("isNavigationLinkCurrent", () => {
  it("marks the new-research link current only on that page", () => {
    const link = { href: "/research/new/", match: "exact" } as const;

    expect(isNavigationLinkCurrent("/research/new/", link)).toBe(true);
    expect(isNavigationLinkCurrent("/research/session-123", link)).toBe(false);
    expect(isNavigationLinkCurrent("/research/workspace/", link)).toBe(false);
  });
});
