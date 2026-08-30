# Runbook : traiter une chaîne YouTube

Procédure pour ajouter une chaîne au corpus et régénérer le catalogue. Le template
`run-channel.sh` enchaîne tout. Ce README explique les décisions et les pièges, pour
qu'une prochaine itération ne les redécouvre pas.

## Démarrage rapide

```bash
# Chaîne entière (tous les ans)
./runbook/run-channel.sh bytebytego https://www.youtube.com/@ByteByteGo/videos

# Filtrée sur 2025 et 2026, en gardant la piste française
KEEP_LANG=fr ./runbook/run-channel.sh alafrench https://www.youtube.com/@alafrench/videos '2025|2026'

# Chaîne anglophone : garder l'anglais
KEEP_LANG=en ./runbook/run-channel.sh bloomberg https://www.youtube.com/@markets/videos
```

Le script fait quatre choses : résoudre la liste de vidéos (avec filtre année optionnel),
télécharger transcripts + insights dans `output/<slug>/`, dédupliquer les pistes de langue
si demandé, puis relancer `scripts/build_index.py` pour mettre à jour `output/INDEX.md` et
`output/CATALOG.yaml`.

## Ce qui a coûté du temps, et pourquoi le template le règle

**Cookies Safari.** yt-dlp accepte `--cookies-from-browser safari`, mais macOS interdit la
lecture de `Cookies.binarycookies` sans Full Disk Access. Résultat : zéro sous-titre
téléchargé, run avorté. Le template n'utilise aucun cookie. `--sleep-requests 2` réduit
la cadence, mais ne garantit pas l'absence de blocage YouTube. Si la source exige une
session, utilise une copie locale du template et passe explicitement des cookies Chrome.

**Troncature à 10 000 caractères.** `max_transcript_chars` vaut 10000 par défaut. Sur un
podcast d'une heure, ça peut n'analyser que le début. Le template exporte
`YT_INSIGHTS_MAX_TRANSCRIPT_CHARS=60000`. Cette valeur réduit la troncature, mais ne
garantit pas de couvrir un épisode entier. La marge réelle dépend de la longueur du VTT,
du prompt et de la fenêtre de contexte du modèle. Le CLI affiche `USED/TOTAL` avant
chaque appel réel.

**Filtre par année.** Le CLI n'a pas de filtre natif. `--flat-playlist` liste vite mais ne
donne pas les dates. Le template fait une passe métadonnées (`yt-dlp --print upload_date|id`)
puis filtre sur le préfixe `YYYYMMDD`. C'est lent sur une grosse chaîne (une requête par
vidéo), rapide sur 30 vidéos. Sans filtre, on passe l'URL de chaîne directement à
`yt-insights run`, bien plus rapide.

**Pistes de langue en double.** YouTube sert souvent plusieurs sous-titres (fr, en auto,
fr-orig). Chaque piste produit un insight, donc des doublons par vidéo. La règle : garder la
langue d'origine. Chaîne française → `KEEP_LANG=fr` (on vire l'anglais auto-traduit). Chaîne
anglophone comme Bloomberg → `KEEP_LANG=en`. Sans `KEEP_LANG`, rien n'est supprimé et le
catalogue comptera les doublons.

## Backend LLM

Ce template, contrairement au résolveur automatique du CLI, force Ollama en local avec
`devstral-64k:latest` et `http://127.0.0.1:11434/v1`. Vérifie que ce nom de modèle est
bien installé avant le run : `curl -s http://127.0.0.1:11434/api/tags`.

Pour une qualité d'extraction supérieure, viser cc-bridge ou l'API Anthropic :

```bash
YT_MODEL="anthropic/github_copilot/gpt-5-mini" YT_BASE_URL="http://127.0.0.1:4141/v1" \
  ./runbook/run-channel.sh <slug> <url>
```

Attention au piège cc-bridge documenté dans `CLAUDE.md` : utiliser le format passerelle
complet (`anthropic/...`), un ID de modèle nu déclenche l'`active_route` et un 401.

## Structure de sortie attendue

```
output/<slug>/
  transcripts/        VTT (une piste par vidéo après dédup)
  insights/           <video>.json + <video>.md, plus AGGREGATE_REPORT / FULL_REPORT
  INDEX.md            récap par vidéo, trié par date, chemins absolus
  run.log             log du dernier run
```

`scripts/build_index.py` régénère les trois niveaux d'un coup : les `INDEX.md` par chaîne, le
`output/CATALOG.yaml` (feed machine avec index inversé topic vers chaînes) et le
`output/INDEX.md` global. Idempotent, aucun LLM.

## Reprise et incréments

Le pipeline vérifie son cache : relancer sur une chaîne déjà traitée ne réanalyse que les
vidéos manquantes. Pour forcer une réanalyse, ajouter `--force` au `yt-insights run` dans le
template. Après tout ajout, un simple `python3 scripts/build_index.py` suffit à remettre le
catalogue à jour.

## Fichiers de ce dossier

Les `run-*.sh` nommés d'après une chaîne (`run-martignole.sh`, `run-bloomberg-batches.sh`…)
sont des runners personnels, non versionnés. Seuls `run-channel.sh` (le template générique) et
ce README sont suivis par git.
