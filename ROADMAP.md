# Roadmap produit YT Insights

**Mise à jour :** 2026-08-28
**Statut du lot actif :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Readiness éditoriale :** `UNKNOWN` ; ce statut bloque la promotion produit, pas l'implémentation autorisée.

La CLI, l'index complet et le MCP sont utilisables localement. Le statut
`UNKNOWN` porte uniquement sur la pertinence éditoriale des classements, qui
n'a pas encore reçu de jugement humain. Le [diagramme d'implémentation et guide
de test](docs/IMPLEMENTATION-STATUS.md) donnent la vue opérationnelle actuelle.

## État historique avant ce lot

- Baseline historique : **121 tests `PASS`** au snapshot `aebd6a474244bdf000544a076675ba43481f90f5`, avec `PYTHONPATH=src pytest -q`.
- Cette commande n'est pas rejouée par cette mise à jour documentaire. Le coordinateur consigne séparément tout résultat post-lot.
- [Évidence historique de la tranche VTT](plans/evidence/2026-08-27-search-vertical-slice.md) : contexte, worktree et commandes de vérification antérieures. Elle doit être lue avec le snapshot Git ci-dessus, sans confondre ses résultats avec une nouvelle exécution à 121 tests.
- La tranche technique de **50 VTT** est livrée, mais l'échantillon n'est pas représentatif et sa pertinence éditoriale reste `UNKNOWN`.

## Vérité des données

YT Insights récupère les **sous-titres VTT publiés par YouTube**. Le pipeline actuel ne traite pas l'audio.

| Élément | Rôle | Règle |
|---|---|---|
| Fichiers VTT YouTube | Source de vérité du texte et des timestamps | Les passages en dérivent directement |
| `catalog.sqlite3` | Inventaire, import et repli pour retrouver une vidéo | Ne fournit ni texte, ni passages, ni timestamps à l'index de recherche |
| `search-v1.sqlite3` | Index dérivé des passages et timestamps VTT | Existe et se reconstruit indépendamment ; sa promotion full-corpus comme référence produit reste bloquée tant que la pertinence est `UNKNOWN` |

Les métadonnées vidéo servent à relier les résultats à YouTube. Elles ne remplacent jamais le VTT comme preuve textuelle.

## Séquence et droits d'exécution

| Ordre | Phase | Implémentation et tests | Promotion, exposition ou readiness produit |
|---:|---|---|---|
| P0 | Documentation de vérité | `TERMINÉE` | Aucune promotion de gate humaine |
| P1 | Corriger le resolver Ollama | `TERMINÉE`, tests et revue passés | Correctif technique prêt, sans génération LLM réelle dans ce lot |
| P2 | Recherche utile et performance sur 50 VTT | `TERMINÉE TECHNIQUEMENT` | `UNKNOWN` tant que l'artefact P2 ne contient pas de jugement humain |
| P3 | Corpus complet | `TERMINÉE TECHNIQUEMENT` | Promotion comme référence produit full-corpus bloquée par P2 `UNKNOWN` |
| P4 | MCP minimal | `TERMINÉE TECHNIQUEMENT` | Exposition comme accès produit prêt bloquée par P2 `UNKNOWN` |
| P5 | Installation | `TERMINÉE TECHNIQUEMENT` | Installation promue ou recommandée aux usages produit bloquée par P2 `UNKNOWN` |

L'[artefact P2](plans/evidence/2026-08-28-p2-50-vtt-evaluation.md) est suivi dans le repo, mais son évaluation reste non remplie. P2-S1 y renseigne le manifeste ordonné des 50 VTT, son hash, les requêtes et résultats ; P2-S2 y ajoute les jugements humains. L'absence de jugement reste `UNKNOWN` : elle interdit la promotion, jamais le développement autorisé de P3 à P5.

La [preuve full-corpus](plans/evidence/2026-08-28-full-corpus-benchmark.md)
atteste 3 270 documents, 183 789 passages, un build en 48,75 s, un p95
chaud de 13,806 ms et une recherche dans un nouveau processus en 0,32 s.
Cette dernière mesure conserve le cache disque du système. Ces résultats
valident la capacité technique, pas la pertinence éditoriale.

## Flux cible : deux branches indépendantes

```text
VTT YouTube + métadonnées vidéo
        ├──► catalog.sqlite3 : inventaire/import/repli vidéo
        │
        └──► P2 : échantillon, recherche, pertinence et performance
                    ↓
              search-v1.sqlite3 : index dérivé des passages/timestamps
                    ↓
              P3 corpus complet, P4 MCP minimal, P5 installation
                    ↓
              promotion produit seulement si P2 est jugée explicitement
```

`catalog.sqlite3` ne précède pas et n'alimente pas `search-v1.sqlite3`.

## Déclenchement à la demande d'article

Les packs et exports ne sont pas une phase active. Ils démarrent seulement lorsqu'un article réel nécessite une sélection sourcée et nomme son angle ainsi que les passages à conserver.

## Évolutions conditionnelles

| Évolution | Déclencheur mesuré obligatoire |
|---|---|
| UI locale | Friction répétée et documentée dans des usages réels |
| Extension navigateur | Besoin récurrent non couvert par la CLI/MCP local pour des articles réels |
| Produit hébergé, API, multi-utilisateur | Besoin explicite de partage ou d'accès distant, avec décision sécurité/exploitation séparée |
| Embeddings et recherche hybride | Échecs lexicaux mesurés, notamment sur synonymes et paraphrases |
| Graphe | Questions multi-hop nommées non résolues par passages et filtres |
| Qdrant | Embeddings adoptés et budget SQLite non tenu après profilage |

Ces pistes restent conditionnelles et n'ouvrent aucune promotion sans leur déclencheur.

## Prochaines décisions, dans l'ordre

| Priorité | Décision | Critère de sortie |
|---:|---|---|
| 1 | Revoir humainement l'artefact P2 | Statut explicite `PASS` ou `FAIL`, avec jugements enregistrés |
| 2 | Utiliser CLI et MCP sur un article réel | Angle, requêtes, passages conservés et frictions consignés |
| 3 | Corriger la première friction mesurée | Petit lot avec test d'acceptation, sans ouvrir une plateforme hébergée par défaut |
| 4 | Évaluer MLX direct, UI ou extension | Besoin confirmé par les usages des étapes 1 à 3 |
| 5 | Étudier hybride, graphe ou Qdrant | Échec mesuré de FTS5 ou limite SQLite reproduite |

## Documents de référence

- [Plan consolidé V2](plans/2026-08-27-CONSOLIDATED-v2.md)
- [Index et suivi](plans/README.md)
- [Sessions parallèles](plans/PARALLEL-SESSIONS.md)
- [État d'implémentation et tests](docs/IMPLEMENTATION-STATUS.md)
- [Changelog](CHANGELOG.md)
