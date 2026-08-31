# Index des plans

**Mise à jour :** 2026-08-31

**Workflow cumulatif projet :** implémenté, validation locale `PASS`

**Activation globale :** `false`

## Autorité actuelle

| Ordre | Document | Usage |
|---:|---|---|
| 1 | [Roadmap](../ROADMAP.md) | Fonctionnalités livrées, gates et ordre restant |
| 2 | [État d'implémentation](../docs/IMPLEMENTATION-STATUS.md) | Diagramme, tests et limites observées |
| 3 | [Spécification cumulative](../docs/superpowers/specs/2026-08-31-cumulative-research-workflow-design.md) | Contrat produit et déclencheurs différés |
| 4 | [Plan cumulatif](../docs/superpowers/plans/2026-08-31-cumulative-research-workflow.md) | Séquence d'implémentation et propriétaires |
| 5 | [Architecture Claude Code et Codex](specs/AGENT-PLATFORM.md) | CLI, MCP, quatre skills et installation sûre |
| 6 | [Sessions parallèles](PARALLEL-SESSIONS.md) | État des lots et handoff |

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

## Gates mesurées

| Gate | Statut | Mesure |
|---|---|---|
| Pertinence | `UNKNOWN` | 0 des 20 jugements humains disponibles |
| Découverte | `PASS` | 3 sujets, 10 candidats chacun |
| Refresh | `PASS` | 5 builds, p95 `47.122951 s`, 3 332 documents, 184 636 passages |
| YouTube live | `UNKNOWN` | Non exécuté dans ce lot |
| Claude Code frais | `UNKNOWN` | Non exécuté |
| Codex frais | `UNKNOWN` | Non exécuté |
| Activation globale | `false` | Aucune promotion du quatrième skill |
| Qualité locale | `PASS` | 844 tests + 10 subtests, Ruff complet, `mypy src` sur 44 fichiers et `git diff --check` |
| GitHub CI hébergée | `PASS` sur `906786e` | [Run 33413953735](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33413953735), Python 3.11, Python 3.12 et packaging/runtime `PASS` |

[Artefacts JSON et Markdown](evidence/2026-08-31-cumulative-research-gates.md)

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

Web UI, extension, API hébergée, MCP writable, vectoriel, graphe et acquisition
automatique restent conditionnels. Aucun de ces éléments n'est implémenté ou
autorisé par les plans actuels.

Le run GitHub CI `33413953735` a passé sur `906786e`. Cette preuve ne couvre pas
les commits ultérieurs; les autres preuves mentionnées ici restent locales.
