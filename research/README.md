# Research evidence dossiers

`research export` produces a deterministic, two-file evidence dossier from an already stored research session. It does not retrieve sources, read transcript bodies, generate an article, or call a model.

Each dossier contains `manifest.json` and `dossier.md`. The manifest records the bounded stored evidence, source hashes, acquisition outcomes, coverage limits, and the SHA-256 of the Markdown document. Re-running the export for identical stored state and package version produces identical bytes.

Choose an explicit absolute output directory. An existing directory is rejected unless `--force` is used, and force only replaces a validated prior two-file dossier.
