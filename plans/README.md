# Index des plans

**Mise à jour :** 2026-09-01

**Workflow cumulatif projet :** implémenté, validation locale `PASS`

**Activation globale :** `false`

## Autorité actuelle

| Ordre | Document | Usage |
|---:|---|---|
| 1 | [Récapitulatif de livraison](../docs/DELIVERY-RECAP.md) | Produit livré, architecture, sécurité et preuves finales |
| 2 | [Roadmap](../ROADMAP.md) | Fonctionnalités livrées, gates et ordre restant |
| 3 | [État d'implémentation](../docs/IMPLEMENTATION-STATUS.md) | Diagramme, tests et limites observées |
| 4 | [Spécification cumulative](../docs/superpowers/specs/2026-08-31-cumulative-research-workflow-design.md) | Contrat produit et déclencheurs différés |
| 5 | [Plan cumulatif](../docs/superpowers/plans/2026-08-31-cumulative-research-workflow.md) | Séquence d'implémentation et propriétaires |
| 6 | [Architecture Claude Code et Codex](specs/AGENT-PLATFORM.md) | CLI, MCP, quatre skills et installation sûre |
| 7 | [Sessions parallèles](PARALLEL-SESSIONS.md) | État des lots et handoff |

## État cumulatif

| Lot | Résultat | État |
|---|---|---|
| 0 | Gates rejouables | Pertinence `UNKNOWN`, découverte `PASS`, refresh `PASS` |
| 1 à 4 | Modèles, store, évaluation, découverte | Implémentés |
| 5 et 6 | CLI durable, décisions et candidats | Implémentés |
| 7 | Acquisition exacte, refresh unique et reprise | Implémenté |
| 8 | Dossier déterministe et export projet | Implémenté |
| 9 | Quatrième skill, prompts anglais et assets-only | Implémenté dans le dépôt, pas globalement |
| 10 | E2E, qualité et documentation | Terminé et intégré |
| Web API | Read models, jobs, API versionnée et serveur loopback | Implémenté et packagé |
| Web Astro | Dashboard, recherche, sources, research et exports | Implémenté et validé dans le navigateur |

## Gates mesurées

| Gate | Statut | Mesure |
|---|---|---|
| Pertinence | `UNKNOWN` | 0 des 20 jugements humains disponibles |
| Découverte | `PASS` | 3 sujets, 10 candidats chacun |
| Refresh | `PASS` | 5 builds, p95 `47.122951 s`, 3 332 documents, 184 636 passages |
| YouTube live | `UNKNOWN` | Non exécuté dans ce lot |
| Claude Code frais | `UNKNOWN` | Non exécuté |
| Codex frais | `PASS` | Skill projet et limites d'approbation validés dans un processus éphémère read-only |
| Activation globale | `false` | Aucune promotion du quatrième skill |
| Qualité locale | `PASS` | 1 089 tests Python + 10 subtests, 155 tests frontend, 4 parcours Playwright, Ruff, Astro Check, Mypy sur 53 fichiers et diff-check |
| GitHub CI hébergée | `PASS` sur `b62adaa` | [Run 33494963306](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33494963306), web, Python 3.11, Python 3.12 et packaging/runtime `PASS` |

[Artefacts JSON et Markdown](evidence/2026-08-31-cumulative-research-gates.md)
et [canaris clients frais](evidence/2026-08-31-fresh-client-canaries.md)

## Plans historiques conservés

Ces documents expliquent les choix précédents. Leurs statuts internes ne
remplacent pas la roadmap actuelle.

| Document | Rôle actuel |
|---|---|
| [Baseline](2026-08-27-00-test-baseline.md) | Historique des premiers tests |
| [Manifeste et passages](2026-08-27-01-corpus-manifest.md) | Référence du socle de corpus |
| [Recherche FTS5](2026-08-27-02-fts5-search.md) | Référence de recherche lexicale |
| [Évaluation](2026-08-27-03-evaluation-observability.md) | Protocole historique, jugement humain incomplet |
| [Questions horodatées](2026-08-27-04-questions-provenance.md) | Option non lancée |
| [Spike hybride](2026-08-27-05-hybrid-search-spike.md) | Conditionnel à un échec lexical mesuré |
| [UI locale](2026-08-27-06-local-search-ui.md) | Conditionnelle à une friction réelle |
| [Qdrant](2026-08-27-07-qdrant-scale.md) | Conditionnel aux embeddings et limites SQLite |
| [Tranche verticale](2026-08-27-08-search-vertical-slice.md) | Historique du premier index 50 VTT |
| [Runtime agentique](2026-08-28-09-agent-ready-runtime.md) | Socle CLI, paths, backends et MCP |
| [Intégration globale](2026-08-28-10-claude-codex-global-integration.md) | Protocole digest et rollback |
| [Hébergement et extension](2026-08-28-11-hosted-extension.md) | Option conditionnelle |
| [Plan consolidé V2](2026-08-27-CONSOLIDATED-v2.md) | Contrats historiques |

## Travail non lancé

Interface web hébergée, extension, API distante, MCP writable, vectoriel,
graphe et acquisition automatique restent conditionnels. L'interface locale
Astro est livrée et ne constitue pas une surface réseau partageable.

Le run GitHub CI `33494963306` a passé sur `b62adaa` et couvre le lot web final.
