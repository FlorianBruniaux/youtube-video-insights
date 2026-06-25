---
name: yt-get-insights
description: Analyse les insights d'une vidéo YouTube depuis son VTT existant. Vérifie le cache JSON avant de relancer le LLM. Affiche sujet, points clés, outils et citations. Usage : /yt-get-insights [URL YouTube]
---

## 1. Vérifier les prérequis

Extraire le VIDEO_ID (11 caractères après `v=` dans l'URL). Vérifier que le VTT est en cache dans `output/transcripts/`. Si absent, invoquer `/yt-get-transcript` d'abord.

## 2. Vérifier le cache insights

Chercher `output/insights/*VIDEO_ID*.json`. Si le fichier existe déjà, lire directement le `.md` correspondant et passer au point 4.

## 3. Lancer l'analyse

Lancer `yt-insights run` sur l'URL avec le flag `skip-download` (le VTT est déjà présent, pas besoin de re-télécharger).

Répertoire de travail : `/Users/florianbruniaux/Sites/perso/yt-insights`

## 4. Afficher le résultat

Lire `output/insights/*VIDEO_ID*.md` et présenter :

- **Sujet** : de quoi parle la vidéo en une phrase
- **Points clés** : liste des 3 à 5 insights principaux
- **Outils mentionnés** : technologies ou méthodes citées
- **Citation forte** : verbatim marquant si disponible

Donner le chemin complet du fichier MD pour que l'utilisateur puisse le consulter.
