import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  outDir: "../src/yt_insights/web/static",
  build: {
    format: "directory",
    inlineStylesheets: "never",
  },
  vite: {
    build: {
      assetsInlineLimit: 0,
      sourcemap: false,
    },
  },
});
