# Roadmap produit YT Insights

**Mise à jour :** 2026-08-31

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
| Assistants Claude Code et Codex | Assets projet implémentés | Quatrième skill non installé globalement, canaris frais `UNKNOWN` |
| MCP | Quatre outils read-only | Aucun outil de mutation ou d'orchestration |

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
| Codex frais | `UNKNOWN` | Asset statique validé, pas de session fraîche probante |
| Activation globale | `false` | Aucune installation globale du quatrième skill ou du runtime |
| Qualité locale | `PASS` | 844 tests + 10 subtests, Ruff complet, Mypy sur 44 fichiers source et diff-check |
| GitHub CI hébergée | `PASS` sur `906786e` | [Run 33413953735](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33413953735) : Python 3.11, Python 3.12 et packaging/runtime `PASS` |

Un `UNKNOWN` ne bloque pas une acquisition locale de 1 à 5 IDs explicitement
approuvés. Il bloque toute affirmation de qualité validée ou d'activation
globale.

## Ordre de travail restant

| Priorité | Action | Critère de sortie |
|---:|---|---|
| 1 | Faire la revue humaine de pertinence | Exactement 20 résultats jugés, seuil de passage 16/20 |
| 2 | Exécuter le canari YouTube live | Acquisition réelle bornée, résultat et erreurs consignés |
| 3 | Exécuter les canaris clients frais | Claude Code et Codex découvrent le quatrième skill et respectent les deux confirmations |
| 4 | Préparer un candidat global inerte | Préimages, diff expurgé, digest, rollback et approbation exacte |
| 5 | Tester des sessions de recherche réelles | Dossiers utiles, limites de couverture et frictions consignées |

La validation locale assemblée a passé `844` tests et `10` subtests, Ruff sur
`src tests scripts`, Mypy sur les 44 fichiers de `src`, et `git diff --check`.
Ce résultat ne signifie pas que `mypy --strict` passe. Le workflow GitHub CI a
passé sur le SHA publié `906786e` via le run `33413953735`. Ce résultat est lié
à ce SHA et ne valide aucun commit ultérieur.

## Évolutions conditionnelles

| Évolution non livrée | Déclencheur avant conception |
|---|---|
| API YouTube officielle | Usage hébergé ou plus de 10 % d'échecs sur 30 découvertes locales |
| Indexation incrémentale | p95 du refresh complet supérieur à 60 secondes |
| MCP writable | Friction CLI documentée dans au moins 5 sessions réelles |
| Interface web | Au moins 10 sessions réussies et besoin de partage distant confirmé |
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
- [Architecture Claude Code et Codex](plans/specs/AGENT-PLATFORM.md)
- [Suivi des sessions parallèles](plans/PARALLEL-SESSIONS.md)
- [Plan conditionnel hébergé et extension](plans/2026-08-28-11-hosted-extension.md)
