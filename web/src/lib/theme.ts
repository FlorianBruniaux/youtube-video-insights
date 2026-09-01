export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "yt-insights-theme";

export function applyStoredTheme(storage: Storage, root: HTMLElement): Theme {
  const storedTheme = storage.getItem(THEME_STORAGE_KEY);
  const theme: Theme = storedTheme === "light" ? "light" : "dark";

  root.dataset.theme = theme;
  return theme;
}
