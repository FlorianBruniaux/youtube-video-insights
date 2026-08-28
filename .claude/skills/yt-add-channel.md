---
name: yt-add-channel
description: Ajoute une chaîne YouTube entière au corpus : résout la liste de vidéos (filtre année optionnel), télécharge transcripts + insights dans output/<slug>/, déduplique les pistes de langue, régénère les index globaux, puis rafraîchit le catalogue SQLite. Usage : /yt-add-channel [URL chaîne] [slug] [années]
disable-model-invocation: true
---

Commande de compatibilité explicite. Pour le routage agent courant, utiliser `youtube-acquire`.

Ce skill traite une chaîne complète, pas une vidéo isolée. Pour une seule vidéo, utiliser
`/yt-run-pipeline`. Il s'appuie sur `runbook/run-channel.sh` et `runbook/README.md`, qui
contiennent les pièges détaillés.

## 1. Déterminer les paramètres

- **URL** : l'onglet `/videos` de la chaîne (exclut les Shorts).
- **slug** : nom court du dossier de sortie (`output/<slug>/`). Le déduire du handle si non fourni.
- **années** : regex optionnelle comme `2025|2026`. Vide = toute la chaîne.
- **langue** : déterminer la langue d'origine de la chaîne (FR ou EN). Elle sert au `KEEP_LANG`.

Si un paramètre manque et n'est pas évident, demander à l'utilisateur avant de lancer.

## 2. Vérifier le backend LLM

L'ordre de détection est cc-bridge (port 4141) → Ollama (port 11434) → variable
`ANTHROPIC_API_KEY`. Vérifier celui qui est censé être actif sur cette machine :

- Ollama : `curl -s http://127.0.0.1:11434/api/tags` doit répondre et lister un modèle de chat.
- Anthropic API : `echo $ANTHROPIC_API_KEY` ne doit pas être vide.

Si aucun des deux n'est disponible, le signaler à l'utilisateur avant de lancer quoi que ce soit.

## 3. Estimer le volume avant de lancer

Pour une chaîne inconnue, résoudre d'abord le nombre de vidéos dans la fenêtre demandée :

```bash
.venv/bin/yt-dlp --flat-playlist --no-warnings --print "%(id)s" "<URL>" 2>/dev/null | wc -l
```

Annoncer le volume. Au-delà de ~200 vidéos, prévenir que le run est long (plusieurs heures en
local) et proposer de lancer en tâche de fond.

## 4. Lancer le pipeline

Répertoire de travail : la racine du repo (le dossier où se trouve `pyproject.toml`).

```bash
KEEP_LANG=<fr|en> ./runbook/run-channel.sh <slug> <URL> '<années>'
```

Sur un gros volume, lancer en arrière-plan et surveiller `output/<slug>/run.log` (compter les
`.json` produits dans `output/<slug>/insights/`).

## 5. Vérifier et rendre compte

Rafraîchir le catalogue SQLite après la génération des index :

```bash
.venv/bin/yt-insights catalog import-corpus output
```

- Couverture : nombre de vidéos attendues contre nombre d'insights `.json` produits.
- Structure : `output/<slug>/` doit contenir `transcripts/`, `insights/` et `INDEX.md`.
- Catalogue : confirmer que la chaîne apparaît dans `output/CATALOG.yaml` (bloc `channels:` et
  `topics_index`) et dans `output/INDEX.md`.
- SQLite : exécuter `.venv/bin/yt-insights catalog stats`, puis consulter
  `.venv/bin/yt-insights catalog errors` si l'import termine avec `status=partial`.

Donner à l'utilisateur : le compte final, le chemin de `output/<slug>/INDEX.md`, et signaler
tout échec d'extraction (vidéos sans insight, souvent des transcripts trop courts ou une
troncature `max_tokens`).
