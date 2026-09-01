import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

const bootstrapPath = resolve(process.cwd(), "public/_astro/theme-bootstrap.js");

describe("the pre-paint theme bootstrap", () => {
  it("defaults to dark when no preference has been stored", async () => {
    const source = await readFile(bootstrapPath, "utf8");
    const root = { dataset: { theme: "light" } };

    runInNewContext(source, {
      document: { documentElement: root },
      localStorage: { getItem: () => null },
    });

    expect(root.dataset.theme).toBe("dark");
  });

  it("applies a stored dark theme without requiring the body", async () => {
    const source = await readFile(bootstrapPath, "utf8");
    const root = { dataset: { theme: "light" } };

    runInNewContext(source, {
      document: { documentElement: root },
      localStorage: { getItem: () => "dark" },
    });

    expect(root.dataset.theme).toBe("dark");
  });
});
