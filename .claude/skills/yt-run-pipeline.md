---
name: yt-run-pipeline
description: Pipeline complet pour une vidéo YouTube : transcript, insights et suggestions de Shorts enchaînés en séquence, avec sélection interactive du clip final. Usage : /yt-run-pipeline [URL YouTube]
---

## Séquence

Invoquer dans l'ordre :

1. `/yt-get-transcript` : télécharger le VTT (ou confirmer qu'il est en cache)
2. `/yt-get-insights` : extraire et afficher les insights
3. `/yt-get-shorts` : suggérer les moments, demander le choix, télécharger le clip

Chaque étape vérifie son cache avant de lancer une opération coûteuse. Le pipeline peut être relancé sans risque sur une vidéo déjà partiellement traitée.

## Résumé final

Une fois les 3 étapes terminées, afficher un bilan :

- Chemin du VTT
- Chemin des insights MD
- Chemin du clip téléchargé et sa taille
