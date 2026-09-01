export interface NavigationLinkMatch {
  href: string;
  match: "exact" | "section";
}

export function isNavigationLinkCurrent(
  currentPath: string,
  link: NavigationLinkMatch,
): boolean {
  return link.match === "exact" ? currentPath === link.href : currentPath.startsWith(link.href);
}
