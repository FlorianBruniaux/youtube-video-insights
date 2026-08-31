# Plateforme Claude Code et Codex

**Mise à jour :** 2026-08-31

**Périmètre :** intégration locale et portable de YT Insights

**Activation globale du workflow cumulatif :** `false`

## Décision

Claude Code et Codex consomment le même contrat `yt-insights research`. La CLI
porte l'état, les validations, les écritures et la reprise. Les skills portent
les instructions humaines. Le MCP reste une façade de recherche locale en
lecture seule.

Cette séparation évite trois implémentations divergentes et rend les deux
confirmations humaines testables.

## Surfaces

| Surface | Responsabilité | Interdit |
|---|---|---|
| CLI | Acquisition, évaluation, décisions, découverte, refresh, retry et dossier | Décider que les preuves suffisent |
| MCP | Lire corpus, vidéos et passages | Toute mutation, shell ou SQL brut |
| Skills portables | Guider l'utilisateur et appeler la CLI ou le MCP | Réimplémenter la logique produit |
| Chercheur natif | Recherche longue et sourcée sur le corpus existant | Acquisition ou dossier writable |
| Hooks | Aucun routeur YouTube implicite supplémentaire | Acquisition automatique |

## Skills communs

| Skill | Cas d'usage | Processus |
|---|---|---|
| `youtube-acquire` | Ajouter une source connue après preview | Session principale |
| `youtube-research` | Chercher dans le corpus existant | MCP read-only ou CLI read-only |
| `youtube-export` | Exporter une source existante | Session principale |
| `youtube-cumulative-research` | Construire un corpus par cycles contrôlés | Session principale, CLI `research` |

Le quatrième skill doit toujours :

1. chercher localement avant le réseau;
2. montrer couverture, fraîcheur, dates et limites;
3. demander si les preuves sont suffisantes;
4. traiter `refresh` comme une autorisation de découverte uniquement;
5. présenter au maximum dix candidats;
6. demander un choix de un à cinq IDs exacts;
7. acquérir seulement ces IDs, réévaluer, puis reposer la question;
8. demander ensuite dossier, brouillon, corpus exporté, les deux, ou rien.

Le statut JSON expose `acquisition_history`, limité aux 100 dernières
tentatives, et
`acquisition_history_truncated`. Chaque tentative contient `attempt_id`,
`status` et `items`; chaque item contient `video_id`, `status`, `error_code` et
`source_sha256`. Les clés d'idempotence, sélecteurs de cookies, transcripts et
diagnostics bruts n'y figurent pas. Un retry de lot partiel reprend uniquement
les items `failed_retryable`; il ne réacquiert aucun résultat terminal déjà
enregistré.

Le dossier déterministe est produit par la CLI. Un brouillon d'article reste
une action explicite de l'assistant et ne devient jamais une source YouTube.

## Données partagées

```text
VTT + metadata
  ├── catalog.sqlite3              inventory
  └── .search/search-v1.sqlite3    timestamped passages

.research/research-v1.sqlite3      sessions and decisions
research/.../dossier.md            deterministic publication
research/.../manifest.json         evidence manifest
```

Claude Code et Codex doivent recevoir les mêmes chemins absolus et utiliser la
même révision de session. Aucun client ne déduit le corpus ou la racine des
dossiers de son répertoire courant.

## MCP read-only

Le serveur expose exactement :

1. `list_corpora`;
2. `search_videos`;
3. `search_passages`;
4. `get_passage`.

Un MCP writable n'est pas implémenté. Il ne sera étudié qu'après cinq sessions
réelles documentant une friction CLI répétée.

## Installation sûre

Le dépôt fournit :

```bash
yt-insights setup assistants --client both \
  --data-root /absolute/path/to/yt-insights-data --dry-run
yt-insights setup assistants --client both \
  --data-root /absolute/path/to/yt-insights-data --apply
yt-insights setup assistants --client both \
  --data-root /absolute/path/to/yt-insights-data --verify
```

Le mode complet gère skills, agents natifs et inscriptions MCP. Il refuse les
préimages différentes et rollback son propre état en cas d'échec partiel.

Le mode suivant ne lit ni ne modifie le MCP :

```bash
yt-insights setup assistants --client both --assets-only --dry-run
yt-insights setup assistants --client both --assets-only --apply
yt-insights setup assistants --client both --assets-only --verify
```

Une installation globale exige toujours un candidat inerte, les empreintes des
préimages, un diff expurgé, un digest d'approbation, une nouvelle lecture des
préimages, un rollback et des canaris frais. Le dépôt et ce document
n'autorisent aucune écriture globale.

## Routage

L'invocation explicite reste la règle. Le précédent corpus disjoint a rejeté le
routeur implicite, qui conservait des activations interdites. Le workflow
cumulatif n'ajoute aucun hook global et aucun agent writable.

## Gates actuelles

| Gate | Statut | Conséquence |
|---|---|---|
| Pertinence humaine | `UNKNOWN` | Ne pas présenter la qualité comme validée |
| Découverte locale | `PASS`, 3 sujets sur 3, 10 candidats chacun | Provider local utilisable expérimentalement |
| Refresh | `PASS`, p95 `47.122951 s` sur 5 builds | Pas d'indexation incrémentale requise |
| YouTube live final | `UNKNOWN` | Pas de claim de comportement live |
| Claude Code frais | `UNKNOWN` | Pas de claim d'activation |
| Codex frais | `UNKNOWN` | Pas de claim d'activation |
| Activation globale | `false` | Quatrième skill et runtime non promus |
| Qualité locale | `PASS` | 844 tests + 10 subtests, Ruff complet, Mypy sur 44 fichiers, diff-check |
| GitHub CI hébergée | `PASS` sur `906786e` | [Run 33413953735](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33413953735), Python 3.11, Python 3.12 et packaging/runtime `PASS` |

La validation Mypy porte sur `mypy src`; aucun passage de `mypy --strict`
n'est revendiqué. Le run hébergé valide uniquement `906786e`. Les gates locales
et hébergées ne prouvent ni YouTube live ni le chargement par des clients frais.

## Ce qui reste hors périmètre

- interface web;
- extension navigateur;
- API hébergée;
- MCP writable;
- recherche vectorielle ou hybride;
- base graphe;
- acquisition automatique.

Ces éléments restent soumis aux déclencheurs de la [roadmap](../../ROADMAP.md).

## Acceptation

La plateforme projet est acceptable lorsque les assets statiques, le wheel et
le setup en HOME temporaire passent, sans invoquer les clients. L'activation
globale exige en plus deux sessions fraîches qui découvrent le quatrième skill,
s'arrêtent aux mêmes questions, et ne transforment jamais `refresh` en
approbation.
