(function applyLanguageBeforePaint() {
  var language = "en";
  try {
    var stored = localStorage.getItem("yt-insights-language");
    if (stored === "en" || stored === "fr") {
      language = stored;
    } else {
      var browserLanguages = Array.isArray(navigator.languages)
        ? navigator.languages
        : [navigator.language];
      if (browserLanguages.some(function isFrench(value) {
        return typeof value === "string" && value.toLowerCase().indexOf("fr") === 0;
      })) {
        language = "fr";
      }
    }
  } catch {
    // English remains available when browser preferences cannot be read.
  }
  document.documentElement.lang = language;
  document.documentElement.dataset.language = language;
})();
