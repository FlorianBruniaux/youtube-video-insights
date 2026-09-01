# Roadmap produit YT Insights

**Mise à jour :** 2026-09-01

**Workflow cumulatif local :** `IMPLÉMENTÉ`, validation locale `PASS`

**Activation globale du nouveau workflow :** `false`

**Pertinence humaine :** `UNKNOWN`

YT Insights est maintenant un corpus YouTube local qui peut grandir par cycles
contrôlés. Une recherche commence toujours dans les données existantes. Le
produit mesure couverture et fraîcheur, demande si le résultat suffit, puis ne
cherche sur YouTube et n'acquiert des sources qu'après deux décisions humaines
distinctes.

## Ce qui est livré

| Capacité | État | Limite explicite |
|---|---|---|
| Acquisition VTT et métadonnées | Implémentée | Confirmation pour les lots, aucune transcription audio |
| Catalogue local | Implémenté | Inventaire dans `catalog.sqlite3`, aucun passage source |
| Recherche horodatée | Implémentée | FTS5 dans `search-v1.sqlite3`, pertinence humaine encore `UNKNOWN` |
| Sessions de recherche | Implémentées | État durable dans `.research/research-v1.sqlite3` |
| Couverture et fraîcheur | Implémentées | Profils déterministes, aucune décision automatique de suffisance |
| Découverte YouTube | Implémentée | Maximum 10 candidats, métadonnées seulement |
| Approbation et acquisition | Implémentées | 1 à 5 IDs exacts, décision séparée de `refresh` |
| Reprise et retry | Implémentées | Historique borné aux 100 dernières tentatives; seuls les items `failed_retryable` d'un lot partiel sont réacquis |
| Dossier de preuves | Implémenté | `dossier.md` et `manifest.json`, jamais réindexés comme sources |
| Assistants Claude Code et Codex | Assets projet implémentés | Codex projet `PASS`; Claude frais `UNKNOWN`; quatrième skill non installé globalement |
| MCP | Quatre outils read-only | Aucun outil de mutation ou d'orchestration |
| Interface web locale | Implémentée | Astro statique packagé, serveur Python sur `127.0.0.1`, aucun partage distant |

## Les quatre couches de données

| Couche | Rôle | Reconstruction |
|---|---|---|
| VTT et métadonnées | Sources de vérité textuelles et horodatées | Jamais dérivées d'un dossier généré |
| `catalog.sqlite3` | Inventaire, provenance, artefacts et erreurs | Depuis les sources locales |
| `.search/search-v1.sqlite3` | Passages FTS5 et liens horodatés | Depuis les VTT |
| `.research/research-v1.sqlite3` | Sessions, évaluations, décisions et tentatives | Historique opérationnel versionné par schéma |

Les dossiers exportés sont des publications déterministes. Ils restent séparés
des quatre couches et du corpus source.

## Gates observées

| Gate | Statut | Mesure et portée |
|---|---|---|
| Pertinence | `UNKNOWN` | 0 résultat dans le packet représentatif, donc aucun des 20 jugements humains requis |
| Découverte | `PASS` | 3 sujets, 10 candidats distincts par sujet, état local inchangé |
| Refresh complet | `PASS` | 5 builds validés, p95 `47.122951 s`, 3 332 documents et 184 636 passages |
| YouTube live dans le workflow final | `UNKNOWN` | Aucun canari d'acquisition réel dans ce lot |
| Claude Code frais | `UNKNOWN` | Asset statique validé, pas de session fraîche probante |
| Codex frais | `PASS` | Codex CLI 0.150.1 éphémère, config utilisateur et rules ignorées, sandbox read-only; skill local chargé et deux confirmations restituées |
| Activation globale | `false` | Aucune installation globale du quatrième skill ou du runtime |
| Qualité locale | `PASS` | 1 073 tests Python + 10 subtests, 155 tests frontend, 3 parcours Playwright, Ruff complet, Mypy sur 53 fichiers source, Astro Check sur 51 fichiers et diff-check |
| GitHub CI hébergée | `PASS` sur `000e9b4` | [Run 33414788777](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33414788777) : Python 3.11, Python 3.12 et packaging/runtime `PASS` |

Un `UNKNOWN` ne bloque pas une acquisition locale de 1 à 5 IDs explicitement
approuvés. Il bloque toute affirmation de qualité validée ou d'activation
globale.

## Ordre de travail restant

| Priorité | Action | Critère de sortie |
|---:|---|---|
| 1 | Faire la revue humaine de pertinence | Exactement 20 résultats jugés, seuil de passage 16/20 |
| 2 | Exécuter le canari YouTube live | Acquisition réelle bornée, résultat et erreurs consignés |
| 3 | Terminer le canari client frais | Authentifier Claude Code, puis vérifier le quatrième skill et les deux confirmations; Codex est déjà `PASS` |
| 4 | Préparer un candidat global inerte | Préimages, diff expurgé, digest, rollback et approbation exacte |
| 5 | Tester des sessions de recherche réelles | Dossiers utiles, limites de couverture et frictions consignées |

La validation locale du lot web a passé `1 073` tests Python et `10` subtests,
`155` tests frontend, `3` parcours Playwright, Ruff sur `src tests scripts`,
Mypy sur les 53 fichiers de `src`, Astro Check sur 51 fichiers et
`git diff --check`. Ce résultat ne signifie pas que `mypy --strict` passe. Le
workflow GitHub CI a passé sur l'ancien SHA `000e9b4` via le run `33414788777`.
Cette preuve hébergée ne valide pas le lot web courant.

## Évolutions conditionnelles

| Évolution non livrée | Déclencheur avant conception |
|---|---|
| API YouTube officielle | Usage hébergé ou plus de 10 % d'échecs sur 30 découvertes locales |
| Indexation incrémentale | p95 du refresh complet supérieur à 60 secondes |
| MCP writable | Friction CLI documentée dans au moins 5 sessions réelles |
| Interface web hébergée | Au moins 10 sessions locales réussies et besoin de partage distant confirmé |
| Extension navigateur | Envoi manuel bloquant au moins 10 usages consignés |
| Recherche vectorielle ou hybride | Pertinence sous 80 % après réglage lexical sur un jeu gelé |
| Base graphe | Au moins 3 questions relationnelles impossibles avec passages et métadonnées |
| Acquisition automatique | 20 cycles assistés sans mauvaise approbation et nouveau design opt-in |

Ces déclencheurs ouvrent une discussion. Ils n'autorisent aucune activation
silencieuse.

## Documents de référence

- [État d'implémentation](docs/IMPLEMENTATION-STATUS.md)
- [Spécification du workflow cumulatif](docs/superpowers/specs/2026-08-31-cumulative-research-workflow-design.md)
- [Plan d'implémentation](docs/superpowers/plans/2026-08-31-cumulative-research-workflow.md)
- [Gates mesurées](plans/evidence/2026-08-31-cumulative-research-gates.md)
- [Canaris clients frais](plans/evidence/2026-08-31-fresh-client-canaries.md)
- [Architecture Claude Code et Codex](plans/specs/AGENT-PLATFORM.md)
- [Suivi des sessions parallèles](plans/PARALLEL-SESSIONS.md)
- [Plan conditionnel hébergé et extension](plans/2026-08-28-11-hosted-extension.md)
