import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

const webRoot = process.cwd();
const builtIndex = resolve(webRoot, "../src/yt_insights/web/static/index.html");
const builtWorkspace = resolve(
  webRoot,
  "../src/yt_insights/web/static/research/workspace/index.html",
);

describe("the generated application shell", () => {
  let html = "";
  let workspaceHtml = "";

  beforeAll(async () => {
    process.env.ASTRO_TELEMETRY_DISABLED = "1";
    const { build } = await import("astro");
    await build({ root: webRoot });
    html = await readFile(builtIndex, "utf8");
    workspaceHtml = await readFile(builtWorkspace, "utf8");
  });

  it("keeps styles and scripts external for the server content security policy", () => {
    expect(html).not.toMatch(/<style(?:\s|>)/i);
    expect(html).not.toMatch(/\sstyle\s*=/i);

    const scripts = html.match(/<script\b[^>]*>/gi) ?? [];
    expect(scripts.length).toBeGreaterThan(0);
    expect(scripts.every((tag) => /\ssrc\s*=/.test(tag))).toBe(true);
  });

  it("loads the external theme bootstrap synchronously before the body", () => {
    const bootstrapTag = html.match(
      /<script\b[^>]*\bsrc="\/_astro\/theme-bootstrap\.js"[^>]*><\/script>/i,
    );

    expect(bootstrapTag).not.toBeNull();
    expect(bootstrapTag?.[0]).not.toMatch(/\b(?:async|defer)\b|\btype\s*=/i);
    expect(html.indexOf(bootstrapTag?.[0] ?? "missing")).toBeLessThan(html.indexOf("</head>"));
  });

  it("keeps the mobile research reading order in the generated workspace", () => {
    const markers = [
      "data-evidence-panel",
      "data-decision-panel",
      "data-job-progress",
      "data-candidate-list",
      "data-acquisition-history",
      "data-event-timeline",
    ].map((marker) => workspaceHtml.indexOf(marker));

    expect(markers.every((index) => index >= 0)).toBe(true);
    expect(markers).toEqual([...markers].sort((left, right) => left - right));
  });
});
