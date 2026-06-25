---
name: yt-get-transcript
description: Télécharge le transcript VTT d'une vidéo YouTube via yt-dlp. Vérifie le cache avant de lancer yt-dlp, retente avec les cookies Chrome en cas de 429. Usage : /yt-get-transcript [URL YouTube]
---

## Étape 1 — Extraire le VIDEO_ID

Extraire les 11 caractères après `v=` dans l'URL. Exemple : `nfupYzLjFGc` depuis `https://www.youtube.com/watch?v=nfupYzLjFGc`.

## Étape 2 — Vérifier le cache

```bash
ls /Users/florianbruniaux/Sites/perso/yt-insights/output/transcripts/*VIDEO_ID*.vtt 2>/dev/null
```

Si un fichier existe, afficher son chemin et arrêter. Pas besoin de re-télécharger.

## Étape 3 — Télécharger le VTT

```bash
cd /Users/florianbruniaux/Sites/perso/yt-insights
mkdir -p output/transcripts
yt-dlp --write-auto-subs --sub-langs 'fr' --sub-format vtt --skip-download \
  --ignore-errors \
  -o 'output/transcripts/%(upload_date)s - %(title)s [%(id)s].%(ext)s' \
  'URL'
```

## Si erreur 429 (rate limit)

Retenter avec les cookies Chrome :

```bash
yt-dlp --write-auto-subs --sub-langs 'fr' --sub-format vtt --skip-download \
  --ignore-errors --cookies-from-browser chrome \
  -o 'output/transcripts/%(upload_date)s - %(title)s [%(id)s].%(ext)s' \
  'URL'
```

## Vérification finale

```bash
ls -lh /Users/florianbruniaux/Sites/perso/yt-insights/output/transcripts/*VIDEO_ID*.vtt
```

Afficher : chemin complet du VTT + taille. Si taille < 10KB, signaler que le transcript est probablement vide ou absent pour cette vidéo.
