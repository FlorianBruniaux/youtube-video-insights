import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyLanguage,
  installLanguageControls,
  resolveLanguage,
  translate,
} from "../src/lib/i18n";

describe("language selection", () => {
  beforeEach(() => {
    document.documentElement.lang = "en";
    document.documentElement.dataset.language = "en";
    document.body.replaceChildren();
    window.localStorage.clear();
  });

  it("restores a valid preference before consulting browser languages", () => {
    expect(resolveLanguage("en", ["fr-FR"])).toBe("en");
    expect(resolveLanguage("fr", ["en-US"])).toBe("fr");
    expect(resolveLanguage("de", ["fr-FR", "en-US"])).toBe("fr");
    expect(resolveLanguage(null, ["de-DE", "en-US"])).toBe("en");
  });

  it("translates interface copy without changing unmarked corpus content", () => {
    document.head.innerHTML = `
      <title>Search corpus | YT Insights</title>
      <meta name="description" content="Search timestamped passages in the local YouTube transcript corpus."
        data-i18n-content="Search timestamped passages in the local YouTube transcript corpus.">
    `;
    document.body.innerHTML = `
      <main data-i18n-document-title="Search corpus"></main>
      <p data-i18n="Search">Search</p>
      <input data-i18n-placeholder="Search transcripts" placeholder="Search transcripts">
      <nav data-i18n-aria-label="Primary navigation" aria-label="Primary navigation"></nav>
      <p data-corpus-content>Search</p>
    `;

    applyLanguage("fr", document);

    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dataset.language).toBe("fr");
    expect(document.querySelector("[data-i18n]")?.textContent).toBe("Rechercher");
    expect(document.querySelector("input")?.placeholder).toBe("Rechercher dans les transcriptions");
    expect(document.querySelector("nav")?.getAttribute("aria-label")).toBe("Navigation principale");
    expect(document.title).toBe("Rechercher dans le corpus | YT Insights");
    expect(document.querySelector("meta[name='description']")?.getAttribute("content")).toBe(
      "Rechercher des passages horodatés dans le corpus local de transcriptions YouTube.",
    );
    expect(document.querySelector("[data-corpus-content]")?.textContent).toBe("Search");
    expect(translate("Open dossier", "fr")).toBe("Ouvrir le dossier");
  });

  it("interpolates translated runtime messages with caller-owned values", () => {
    expect(translate("{count} passages found", "fr", { count: 12 })).toBe(
      "12 passages trouvés",
    );
    expect(translate("Created {date} · Session {session}", "fr", {
      date: "2026-09-01 14:00 UTC",
      session: "abc123",
    })).toBe("Créé le 2026-09-01 14:00 UTC · Session abc123");
  });

  it("translates the sponsor copy and accessible link label", () => {
    expect(translate("Infrastructure sponsor", "fr")).toBe("Sponsor infrastructure");
    expect(translate("Neon sponsors YT Insights. Opens in a new tab.", "fr")).toBe(
      "Neon sponsorise YT Insights. S’ouvre dans un nouvel onglet.",
    );
  });

  it("persists a language chosen from the navigation and reloads the current page", () => {
    document.body.innerHTML = `
      <div data-language-control>
        <button type="button" data-language-option="en">EN</button>
        <button type="button" data-language-option="fr">FR</button>
      </div>
    `;
    const reload = vi.fn();
    const cleanup = installLanguageControls(document, window.localStorage, reload);

    document.querySelector<HTMLButtonElement>("[data-language-option='fr']")?.click();

    expect(window.localStorage.getItem("yt-insights-language")).toBe("fr");
    expect(reload).toHaveBeenCalledOnce();
    cleanup();
  });
});
