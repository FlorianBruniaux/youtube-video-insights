# État d'implémentation

**Mise à jour :** 2026-09-01

**Workflow cumulatif :** implémenté, validation locale `PASS`

**Activation globale :** `false`

**GitHub CI hébergée :** `PASS` sur `b62adaa`

Le lot web courant a passé localement `1 089` tests Python et `10` subtests,
`155` tests frontend, `4` parcours Playwright, Ruff sur `src tests scripts`,
Mypy sur les 53 fichiers source, Astro Check sur 51 fichiers et
`git diff --check`. Le [run GitHub Actions 33494963306](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33494963306)
a passé sur le même SHA `b62adaa`, avec les jobs web, Python 3.11, Python 3.12
et packaging/runtime au vert. Mypy n'a pas été exécuté avec `--strict`.

## Vue d'ensemble

```mermaid
flowchart TD
    U[Question de recherche] --> A[Évaluation locale]
    VTT[VTT + métadonnées] --> CAT[catalog.sqlite3]
    VTT --> FTS[search-v1.sqlite3]
    CAT --> A
    FTS --> A
    A --> R[research-v1.sqlite3]
    A --> Q1{Preuves suffisantes ?}
    Q1 -->|Oui| DONE[Recherche terminée]
    DONE -->|Export optionnel| OUT[Dossier déterministe]
    Q1 -->|Refresh demandé| DISC[Découverte, max 10 candidats]
    DISC --> Q2{IDs approuvés ?}
    Q2 -->|Annuler| STOP[Session annulée]
    Q2 -->|Reporter| HOLD[En attente, session reprenable]
    Q2 -->|1 à 5 IDs exacts| ACQ[Acquisition]
    ACQ --> IDX[Publication atomique catalogue + index]
    IDX --> A
    OUT --> COPY[Copie explicite vers le projet courant]
    MCP[MCP, 4 outils read-only] --> CAT
    MCP --> FTS
    SKILL[4 skills portables] --> CLI[CLI commune]
    CLI --> A
    WEB[Astro local, light + dark] --> API[API Python /api/v1]
    API --> CAT
    API --> FTS
    API --> R
```

Source reproductible :
[`docs/assets/cumulative-research-workflow.mmd`](assets/cumulative-research-workflow.mmd).
Le JPG du README illustre le même cycle livré. Le Mermaid reste la source
textuelle révisable; le rendu raster n'est pas généré par le build du dépôt.

## Ce qui fonctionne

| Surface | Comportement implémenté | Limite |
|---|---|---|
| Acquisition | VTT et métadonnées, preview et confirmation des lots | Pas de transcription audio |
| Évaluation locale | Couverture, dates inconnues et fraîcheur par fingerprint exact | Aucun accès réseau, ne décide jamais que le corpus suffit |
| Sessions | État, révisions, événements, tentatives et résultats par vidéo dans `research-v1.sqlite3` | `status --json` expose les 100 dernières tentatives et signale la troncature |
| Découverte | Recherche YouTube par métadonnées après `refresh` | Maximum 10 candidats, aucune acquisition implicite |
| Approbation | IDs vérifiés contre le dernier snapshot | 1 à 5 IDs exacts |
| Acquisition cumulative | Résultats par vidéo, refresh unique, réévaluation | Les sources acquises restent en cas d'échec de publication |
| Retry | Reprend seulement le stage retryable enregistré | Seuls les items `failed_retryable` sont réacquis |
| Dossier | `dossier.md` et `manifest.json` déterministes | Destination absolue, pas de prose LLM ni de réindexation |
| MCP | `list_corpora`, `search_videos`, `search_passages`, `get_passage` | Lecture seule |
| Assistants | Quatre skills projet et assets wheel Claude Code/Codex | Quatrième skill non installé globalement |
| Setup | `--dry-run`, `--apply`, `--verify`, plus `--assets-only` | Une écriture globale demande toujours une transaction approuvée |
| Web local | Dashboard, recherche, sources, sessions, jobs et exports en thème clair ou sombre | Boucle locale `127.0.0.1` uniquement, aucun hébergement ou compte utilisateur |

Le contrat actuel expose `acquisition_history`, limité aux 100 dernières
tentatives, et `acquisition_history_truncated`. Chaque tentative contient
`attempt_id`, `status` et `items`; chaque item contient `video_id`, `status`,
`error_code` et `source_sha256`. Les clés d'idempotence, sélecteurs de cookies,
transcripts et diagnostics bruts restent absents.

L'interface installée démarre avec `yt-insights serve`; la commande
`yt-insights serve --no-open` supprime uniquement l'ouverture automatique du navigateur. Dans les
deux cas, l'écoute reste limitée à `127.0.0.1` et les assets Astro viennent du
package Python, sans runtime Node.js.

## Cycle utilisateur

```text
start -> awaiting_sufficiency_confirmation
  -> sufficient -> completed -> optional export
  -> refresh -> discovering -> discover -> awaiting_candidate_approval
     -> cancel
     -> approve exact IDs -> acquire -> reindex -> reassess
        -> awaiting_sufficiency_confirmation
```

Chaque cycle d'évaluation se termine par la question de suffisance. `refresh`
autorise uniquement la découverte. L'utilisateur choisit ensuite séparément
les candidats à acquérir.

## Les quatre couches

| Donnée | Source ou dérivé | Ne doit jamais contenir |
|---|---|---|
| VTT et métadonnées | Source YouTube locale | Dossier généré |
| `catalog.sqlite3` | Inventaire dérivé | Passages de preuve comme source de recherche |
| `.search/search-v1.sqlite3` | FTS5 dérivé des VTT | Texte de dossier ou article |
| `.research/research-v1.sqlite3` | Historique opérationnel | Transcript complet, cookie ou secret |

## Gates observées

| Gate | Statut | Preuve |
|---|---|---|
| Pertinence | `UNKNOWN` | Le packet représentatif a retourné 0 résultat, donc aucun des 20 jugements requis |
| Découverte | `PASS` | 3 sujets sur 3, 10 candidats par sujet, corpus et bases inchangés |
| Refresh | `PASS` | 5 builds, p95 `47.122951 s`, 3 332 documents, 184 636 passages |
| YouTube live final | `UNKNOWN` | Pas de canari live dans la vérification hermétique |
| Claude Code frais | `UNKNOWN` | Chargement statique seulement |
| Codex frais | `PASS` | Processus éphémère read-only: skill projet chargé, deux décisions et limites 10/5 restituées |
| Activation globale | `false` | Pas de promotion du quatrième skill ou du runtime |
| Qualité locale du lot web courant | `PASS` | 1 089 tests Python + 10 subtests, 155 tests frontend, 4 parcours Playwright, Ruff complet, Mypy sur 53 fichiers et diff-check |
| GitHub CI hébergée | `PASS` sur `b62adaa` | [Run 33494963306](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33494963306), web, Python 3.11, Python 3.12 et packaging/runtime `PASS` |

Les gates externes encore `UNKNOWN` bloquent les affirmations correspondantes
et l'activation globale. Elles ne bloquent pas une acquisition locale
explicitement approuvée.

## Comment tester

### Suite locale

```bash
uv sync --extra mcp --extra dev
pnpm --dir web install --frozen-lockfile
python scripts/verify_web_build.py
pnpm --dir web test
pnpm --dir web check
pnpm --dir web test:e2e
uv run --extra mcp --extra dev pytest -q
uv run --extra dev ruff check src tests scripts
uv run --extra dev mypy src
uv lock --check
git diff --check
```

Ces commandes sont vertes sur le SHA assemblé local. La commande documentée est
`mypy src`; aucun passage de `mypy --strict` n'est revendiqué.

Ces commandes ne prouvent ni YouTube live ni le chargement effectif dans une
session Claude Code ou Codex fraîche.

### Contrat de recherche

```bash
uv run pytest -q \
  tests/research \
  tests/test_cli_research.py \
  tests/test_cumulative_research_gates.py \
  tests/test_agent_assets.py \
  tests/test_assistant_setup.py
```

### Gates et wheel installable

```bash
uv run python scripts/validate_cumulative_research_gates.py \
  plans/evidence/2026-08-31-cumulative-research-gates.json
.venv/bin/python scripts/smoke_wheel.py --offline
```

Le smoke construit le wheel depuis une copie propre, installe le quatrième
skill dans un HOME temporaire, vérifie les dix sous-commandes `research`, puis
teste les quatre outils MCP read-only. Les exécutables Claude et Codex factices
ne doivent pas être invoqués par `--assets-only`.

## Non livré

Interface web hébergée, extension navigateur, API distante, MCP writable, recherche
vectorielle ou hybride, base graphe et acquisition automatique restent hors du
produit actuel. Leurs déclencheurs sont définis dans la
[roadmap](../ROADMAP.md).

## Documents associés

- [Récapitulatif de livraison](DELIVERY-RECAP.md)
- [Installation](../INSTALL.md)
- [Roadmap](../ROADMAP.md)
- [Guide Claude Code et Codex](claude-code.md)
- [Gates mesurées](../plans/evidence/2026-08-31-cumulative-research-gates.md)
- [Spécification](superpowers/specs/2026-08-31-cumulative-research-workflow-design.md)
- [Plan d'implémentation](superpowers/plans/2026-08-31-cumulative-research-workflow.md)
