# Roadmap produit YT Insights

**Mise à jour :** 2026-08-28
**Statut du socle livré :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Statut du runtime pour Claude Code et Codex :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Statut des skills et agents projet :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Statut du candidat global :** `CONSTRUIT ET VALIDÉ EN ENVIRONNEMENT INERTE`.
**Statut du routage implicite :** `FAIL`, 20/30 positifs et 0/15 activations interdites.
**Statut de l'installation globale :** `NON INSTALLÉE`, approbations exactes requises.
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

## Cible Claude Code et Codex

Le produit doit devenir un service local de corpus, utilisable depuis n'importe
quel projet. Claude Code et Codex ne réimplémentent pas YouTube, l'indexation ou
l'export. Ils découvrent trois skills communs, interrogent un MCP en lecture
seule et délèguent toute mutation à la CLI empaquetée.

```text
YouTube URL
    │
    ▼
yt-insights acquire
preview obligatoire pour channel/playlist
    │
    ├──► VTT + métadonnées ──► catalog.sqlite3
    │                    └────► search-v1.sqlite3
    │
    ├──► yt-insights export ──► VTT, TXT ou Markdown sourcé
    │
    └──► MCP read-only
             ├── list_corpora
             ├── search_videos
             ├── search_passages
             └── get_passage
                      │
              ┌───────┴────────┐
              ▼                ▼
        Claude Code          Codex
        3 skills + agent     3 skills + agent
```

### Suivi des lots

| Vague | Statut | Apport concret | Gate |
|---|---|---|---|
| A1 | `TERMINÉ` | Un `data_root` absolu et stable, utilisable hors du repo | Même corpus depuis deux répertoires différents |
| A2 | `TERMINÉ` | `doctor --json` secret-safe | Aucun secret affiché, aucune écriture, diagnostics locaux |
| A3 | `TERMINÉ` | `acquire` unifié avec preview et confirmation des volumes | Aucun channel, playlist ou batch sans confirmation explicite |
| A4 | `TERMINÉ` | `export video` en VTT, TXT et Markdown sans LLM | Sortie déterministe avec URL, identité source et timestamps |
| B1 | `TERMINÉ` | MCP étendu de deux à quatre outils read-only | Smoke réel sur les quatre outils, aucun outil de mutation |
| B2 | `TERMINÉ` | Skills portables `youtube-acquire`, `youtube-research`, `youtube-export` | Trois validateurs officiels et tests de portabilité au vert |
| B3 | `FAIL` | Agents natifs et fixture terminés; benchmark réel à 20/30 positifs, 0/15 interdits, p95 0,709 ms | Atteindre 27/30 sans baisser le seuil global ni ajouter un second hook |
| C1 | `PARTIEL` | Source de release et transactions runtime/intégrations construites dans une copie inerte; 108/108 tests et contre-revue passés | Intégration approuvée dans le dépôt global puis trois digests liés aux nouvelles préimages live |
| C2 | `BLOQUÉ PAR APPROBATION` | Runtime, config, skills, agents et MCP globaux | Même corpus depuis deux cwd, parité de cinq requêtes et restauration testée |
| D1 | `OPTIONNEL` | Choix explicite Ollama, MLX, cc-bridge ou remote | Backend réellement résolu, canari MLX séparé |

Les détails exécutables sont dans le [plan runtime](plans/2026-08-28-09-agent-ready-runtime.md), le [plan d'intégration globale](plans/2026-08-28-10-claude-codex-global-integration.md) et le [suivi parallèle](plans/PARALLEL-SESSIONS.md).

### Politique de backends

| Besoin | Backend par défaut | Pourquoi |
|---|---|---|
| Récupérer les sous-titres | Aucun LLM | YouTube fournit déjà les VTT |
| Rechercher et exporter | Aucun LLM | FTS5 et transformations déterministes suffisent |
| Produire des insights en volume | Ollama local ou cc-bridge | Coût maîtrisé et modèle interchangeable |
| Analyse exigeant plus de qualité | Backend distant explicite | Choix volontaire, jamais un fallback silencieux |
| MLX direct | Lot D1 optionnel après l'intégration agentique | L'implémentation actuelle ne charge pas encore correctement modèle et tokenizer |

La transcription audio n'entre pas dans ce lot. Elle ne devient pertinente que
si une vidéo ne fournit aucun sous-titre exploitable et qu'un cas réel justifie
le coût de téléchargement et de transcription.

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

Le [plan service hébergé et extension](plans/2026-08-28-11-hosted-extension.md)
précise quatre déclencheurs vérifiables. La première version hébergée conserve
SQLite sur un volume persistant. PostgreSQL n'arrive qu'avec un second utilisateur
ou des écritures concurrentes. Les embeddings et une base graphe restent des
index dérivés, ouverts uniquement par un corpus de questions en échec.

## Prochaines décisions, dans l'ordre

| Priorité | Décision | Critère de sortie |
|---:|---|---|
| 1 | Mesurer la fixture sur le routeur global existant | Au moins 27/30 positifs, 0/15 négatifs, aucune nouvelle interception interdite |
| 2 | Construire les trois candidats globaux C1 | Runtime, shared et integrations ont chacun diff expurgé, digest, test inerte et rollback |
| 3 | Installer C2 après approbation explicite | Claude Code et Codex neufs voient les mêmes skills, agent et corpus |
| 4 | Revoir humainement l'artefact P2 sur un article réel | Statut `PASS` ou `FAIL`, passages conservés et frictions consignées |
| 5 | Activer ou rejeter le lot hébergé | Au moins un déclencheur du plan H est prouvé |
| 6 | Étudier hybride, graphe ou Qdrant | Échec mesuré de FTS5 ou limite SQLite reproduite |

## Documents de référence

- [Plan consolidé V2](plans/2026-08-27-CONSOLIDATED-v2.md)
- [Index et suivi](plans/README.md)
- [Sessions parallèles](plans/PARALLEL-SESSIONS.md)
- [État d'implémentation et tests](docs/IMPLEMENTATION-STATUS.md)
- [Changelog](CHANGELOG.md)
- [Architecture Claude Code et Codex](plans/specs/AGENT-PLATFORM.md)
- [Plan runtime agentique](plans/2026-08-28-09-agent-ready-runtime.md)
- [Plan intégration globale](plans/2026-08-28-10-claude-codex-global-integration.md)
- [Plan hébergé et extension](plans/2026-08-28-11-hosted-extension.md)
