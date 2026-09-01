import { describe, expect, it } from "vitest";

import {
  createText,
  createYouTubeWatchLink,
  replaceChildren,
  setText,
} from "../src/lib/dom";

describe("safe DOM helpers", () => {
  it("replaces children with caller-created nodes without interpreting text", () => {
    const target = document.createElement("div");
    target.append(document.createElement("strong"));
    const text = createText('<img src=x onerror="alert(1)">');

    replaceChildren(target, [text]);

    expect(target.childNodes).toHaveLength(1);
    expect(target.textContent).toBe('<img src=x onerror="alert(1)">');
    expect(target.querySelector("img")).toBeNull();
  });

  it("sets untrusted copy as text only", () => {
    const target = document.createElement("p");

    setText(target, "<script>secret()</script>");

    expect(target.textContent).toBe("<script>secret()</script>");
    expect(target.querySelector("script")).toBeNull();
  });

  it("creates a hardened link for an exact HTTPS YouTube watch URL", () => {
    const link = createYouTubeWatchLink(
      "Watch evidence",
      "https://www.youtube.com/watch?v=abc123DEF45&t=12s",
    );

    expect(link?.textContent).toBe("Watch evidence");
    expect(link?.href).toBe(
      "https://www.youtube.com/watch?v=abc123DEF45&t=12s",
    );
    expect(link?.target).toBe("_blank");
    expect(link?.rel).toBe("noopener noreferrer");
  });

  it.each([
    "http://www.youtube.com/watch?v=abc123DEF45",
    "https://m.youtube.com/watch?v=abc123DEF45",
    "https://youtube.com.evil.test/watch?v=abc123DEF45",
    "https://www.youtube.com/embed/abc123DEF45",
    "https://user@www.youtube.com/watch?v=abc123DEF45",
    "https://www.youtube.com:443/watch?v=abc123DEF45",
    "https://www.youtube.com/watch?v=too-short",
    "https://www.youtube.com/watch?v=abc123DEF45&v=abc123DEF46",
    "/watch?v=abc123DEF45",
  ])("rejects a non-exact YouTube watch URL: %s", (url) => {
    expect(createYouTubeWatchLink("Unsafe", url)).toBeNull();
  });
});
