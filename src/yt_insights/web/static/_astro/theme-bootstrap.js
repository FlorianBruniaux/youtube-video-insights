(function applyThemeBeforePaint() {
  var theme = "dark";

  try {
    if (localStorage.getItem("yt-insights-theme") === "light") {
      theme = "light";
    }
  } catch {
    // The default remains usable when browser storage is unavailable.
  }

  document.documentElement.dataset.theme = theme;
})();
