const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const WATCH_PREFIX = /^https:\/\/(?:www\.)?youtube\.com\/watch\?/i;

export function createText(value: string): Text {
  return document.createTextNode(value);
}

export function setText(target: Node, value: string): void {
  target.textContent = value;
}

export function replaceChildren(
  target: Element,
  children: readonly Node[],
): void {
  target.replaceChildren(...children);
}

export function createYouTubeWatchLink(
  label: string,
  value: string,
): HTMLAnchorElement | null {
  const parsed = parseYouTubeWatchUrl(value);
  if (parsed === null) return null;

  const link = document.createElement("a");
  link.href = parsed.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  return link;
}

export function parseYouTubeWatchUrl(value: string): URL | null {
  if (!WATCH_PREFIX.test(value)) return null;

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  const videoIds = parsed.searchParams.getAll("v");
  if (
    parsed.protocol !== "https:" ||
    (parsed.hostname !== "youtube.com" &&
      parsed.hostname !== "www.youtube.com") ||
    parsed.pathname !== "/watch" ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.port !== "" ||
    parsed.hash !== "" ||
    videoIds.length !== 1 ||
    !VIDEO_ID.test(videoIds[0] ?? "")
  ) {
    return null;
  }
  return parsed;
}
