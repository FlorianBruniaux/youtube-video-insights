export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "yt-insights-theme";

export function applyStoredTheme(storage: Storage, root: HTMLElement): Theme {
  const storedTheme = storage.getItem(THEME_STORAGE_KEY);
  const theme: Theme = storedTheme === "dark" ? "dark" : "light";

  root.dataset.theme = theme;
  return theme;
}
