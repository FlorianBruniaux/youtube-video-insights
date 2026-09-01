(function applyThemeBeforePaint() {
  var theme = "light";

  try {
    if (localStorage.getItem("yt-insights-theme") === "dark") {
      theme = "dark";
    }
  } catch {
    // The default remains usable when browser storage is unavailable.
  }

  document.documentElement.dataset.theme = theme;
})();
