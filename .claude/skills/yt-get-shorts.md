---
name: yt-get-shorts
description: Suggère les meilleurs moments pour un YouTube Short, présente les options avec timestamps et verbatim, demande le choix à l'utilisateur, puis télécharge le clip en mp4. Usage : /yt-get-shorts [URL YouTube]
---

## 1. Vérifier les prérequis

Extraire le VIDEO_ID. Vérifier que le VTT est dans `output/transcripts/`. Si absent, invoquer `/yt-get-transcript` d'abord.

## 2. Générer les suggestions

Chercher le cache `output/shorts/*VIDEO_ID*.json`. Si présent, lire directement. Sinon, lancer `yt-insights suggest-shorts` avec le chemin VTT.

Répertoire de travail : `/Users/florianbruniaux/Sites/perso/yt-insights`

## 3. Présenter les options

Pour chaque suggestion du JSON, afficher clairement :

```
Option N  |  Score X/5  |  HH:MM:SS -> HH:MM:SS (Xs)
Hook : "..."
Verbatim : "..."
Pourquoi : ...
```

## 4. Demander le choix

Poser la question explicitement : quelle option (1, 2, 3), ou un moment custom avec start et end précis ?

Ne jamais télécharger sans réponse de l'utilisateur.

## 5. Télécharger le clip

Lancer `yt-insights generate-short` avec le VIDEO_ID, les timestamps choisis, un titre en slug (tirets, pas d'espaces), format mp4.

## 6. Confirmer

Ouvrir le fichier avec `open`. Afficher chemin complet et taille du clip.
