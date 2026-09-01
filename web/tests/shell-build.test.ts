import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

const webRoot = process.cwd();
const builtIndex = resolve(webRoot, "../src/yt_insights/web/static/index.html");

describe("the generated application shell", () => {
  let html = "";

  beforeAll(async () => {
    process.env.ASTRO_TELEMETRY_DISABLED = "1";
    const { build } = await import("astro");
    await build({ root: webRoot });
    html = await readFile(builtIndex, "utf8");
  });

  it("keeps styles and scripts external for the server content security policy", () => {
    expect(html).not.toMatch(/<style(?:\s|>)/i);
    expect(html).not.toMatch(/\sstyle\s*=/i);

    const scripts = html.match(/<script\b[^>]*>/gi) ?? [];
    expect(scripts.length).toBeGreaterThan(0);
    expect(scripts.every((tag) => /\ssrc\s*=/.test(tag))).toBe(true);
  });
});
