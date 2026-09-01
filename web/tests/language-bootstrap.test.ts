import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

const bootstrapPath = resolve(process.cwd(), "public/_astro/language-bootstrap.js");

describe("the pre-paint language bootstrap", () => {
  it("selects French from the browser when no preference exists", async () => {
    const source = await readFile(bootstrapPath, "utf8");
    const root = { dataset: { language: "en" }, lang: "en" };

    runInNewContext(source, {
      document: { documentElement: root },
      localStorage: { getItem: () => null },
      navigator: { languages: ["fr-FR", "en-US"] },
    });

    expect(root.lang).toBe("fr");
    expect(root.dataset.language).toBe("fr");
  });

  it("keeps a stored English preference on a French browser", async () => {
    const source = await readFile(bootstrapPath, "utf8");
    const root = { dataset: { language: "fr" }, lang: "fr" };

    runInNewContext(source, {
      document: { documentElement: root },
      localStorage: { getItem: () => "en" },
      navigator: { languages: ["fr-FR"] },
    });

    expect(root.lang).toBe("en");
    expect(root.dataset.language).toBe("en");
  });
});
