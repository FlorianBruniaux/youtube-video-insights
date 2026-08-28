# YT Insights : index des plans

**Dernière mise à jour :** 2026-08-28
**État du lot actif :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Readiness produit :** `UNKNOWN` jusqu'à la revue humaine P2 ; ce n'est pas un veto de développement.

## À lire maintenant

| Ordre | Document | Rôle |
|---:|---|---|
| 1 | [Roadmap produit](../ROADMAP.md) | Vérité des données, droits d'exécution et déclencheurs |
| 2 | [État d'implémentation](../docs/IMPLEMENTATION-STATUS.md) | Diagramme, fonctions livrées, reste et commandes de test |
| 3 | [Plan consolidé V2](2026-08-27-CONSOLIDATED-v2.md) | Contrats et séparation implémentation/promotion |
| 4 | [Sessions parallèles](PARALLEL-SESSIONS.md) | Propriétés, handoff P2 et ordre de fusion |

## Plans détaillés conservés

Le plan consolidé V2 et la roadmap font autorité. Les documents ci-dessous
gardent les contrats, tests et options étudiés. Leurs cases ou statuts locaux
peuvent refléter la phase de conception initiale et ne remplacent pas le suivi
actif ci-dessus.

| Document | Rôle actuel |
|---|---|
| [00 - Baseline de tests](2026-08-27-00-test-baseline.md) | Historique de l'intégration de la baseline |
| [01 - Manifeste et passages](2026-08-27-01-corpus-manifest.md) | Référence détaillée, socle largement livré |
| [02 - Recherche FTS5](2026-08-27-02-fts5-search.md) | Référence détaillée, socle livré |
| [03 - Évaluation et observabilité](2026-08-27-03-evaluation-observability.md) | Technique livrée en partie ; jugement humain encore `UNKNOWN` |
| [04 - Questions horodatées](2026-08-27-04-questions-provenance.md) | Suite possible, non lancée |
| [05 - Spike hybride](2026-08-27-05-hybrid-search-spike.md) | Conditionnel aux échecs lexicaux mesurés |
| [06 - UI locale](2026-08-27-06-local-search-ui.md) | Conditionnel à une friction d'usage réelle |
| [07 - Qdrant](2026-08-27-07-qdrant-scale.md) | Conditionnel à l'adoption des embeddings et aux limites SQLite |
| [08 - Tranche verticale](2026-08-27-08-search-vertical-slice.md) | Historique de l'implémentation initiale sur 50 VTT |
| [Spécification d'architecture](specs/SEARCH-ARCHITECTURE.md) | Contrats de conception initiaux |
| [Critique architecture et plan](reviews/2026-08-27-architecture-plan-critique.md) | Décisions écartées, risques et garde-fous |

## État attesté

| Sujet | État | Limite |
|---|---|---|
| Baseline historique | `121 passed` au SHA `aebd6a474244bdf000544a076675ba43481f90f5` via `PYTHONPATH=src pytest -q` | Pas rejouée ici ; résultats post-lot par le coordinateur |
| Évidence liée | [Tranche VTT historique](evidence/2026-08-27-search-vertical-slice.md) | Ne pas confondre avec un nouveau run de 121 tests |
| Tranche 50 VTT | Livrée techniquement | Non représentative |
| Corpus complet | [Preuve technique](evidence/2026-08-28-full-corpus-benchmark.md) : 3 270 documents, 183 789 passages, build 48,75 s, p95 chaud 13,806 ms, nouveau processus 0,32 s | Cache disque non purgé ; pertinence humaine non couverte |
| Intégrité de l'index | Reçu lié au `generation_id` et au SHA-256 de la base, cache par instance invalidé par l'identité du fichier et son `ctime` | Corruption et races locales seulement ; pas de protection contre un attaquant du même UID qui réécrit base et reçu |
| Pertinence éditoriale | `UNKNOWN` | Bloque promotion/readiness, pas P3 à P5 en développement |
| Source | Sous-titres VTT YouTube | Le pipeline actuel ne traite pas l'audio |

## Séquence active et promotions

| Phase | Construction et test | Promotion/readiness |
|---|---|---|
| P0 documentation | `TERMINÉE` | Aucune gate humaine |
| P1 resolver Ollama | `TERMINÉE` | Génération LLM réelle non rejouée |
| P2 recherche 50 VTT | `TERMINÉE TECHNIQUEMENT` | `UNKNOWN` sans jugements humains dans l'artefact P2 |
| P3 corpus complet | `TERMINÉE TECHNIQUEMENT` | Référence produit full-corpus bloquée par P2 `UNKNOWN` |
| P4 MCP minimal : `search_passages`, `get_passage` | `TERMINÉE TECHNIQUEMENT` | Accès produit prêt bloqué par P2 `UNKNOWN` |
| P5 installation locale | `TERMINÉE TECHNIQUEMENT` | Promotion/recommandation produit bloquée par P2 `UNKNOWN` |

## Contrat des bases

| Base | Rôle | Limite |
|---|---|---|
| `catalog.sqlite3` | Inventaire, import et repli vidéo | Ne fournit jamais les passages ou timestamps de recherche |
| `search-v1.sqlite3` | Index dérivé des passages et timestamps depuis les VTT | Peut être construit/testé avant P2 ; sa promotion full-corpus dépend de P2 |

Les deux branches sont indépendantes : les VTT alimentent le catalogue et l'index de recherche directement, jamais `catalog.sqlite3` vers `search-v1.sqlite3`.

## Artefact P2 et évolutions conditionnelles

L'[artefact P2](evidence/2026-08-28-p2-50-vtt-evaluation.md) est suivi dans le repo, mais son évaluation reste non remplie. Il lie le manifeste ordonné des 50 VTT, son hash, les requêtes, résultats, jugements et statut `PASS`/`FAIL`/`UNKNOWN`. Voir les [sessions parallèles](PARALLEL-SESSIONS.md) pour son handoff.

Packs/exports nécessitent un article réel. UI, extension, produit hébergé, embeddings, graphe et Qdrant sont conditionnels, selon les déclencheurs de la [roadmap](../ROADMAP.md).
