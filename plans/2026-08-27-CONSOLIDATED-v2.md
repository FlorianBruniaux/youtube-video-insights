# Plan consolidé V2 : YT Insights

**Mise à jour :** 2026-08-28
**Statut du lot actif :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Règle directrice :** P3 à P5 sont autorisées en implémentation et tests ; la pertinence éditoriale `UNKNOWN` bloque leur promotion et leur readiness produit.

## 1. État de départ historique

| Élément | État |
|---|---|
| Baseline | Historique : `121 passed`, snapshot `aebd6a474244bdf000544a076675ba43481f90f5`, commande `PYTHONPATH=src pytest -q` |
| Évidence liée | [Tranche VTT historique](evidence/2026-08-27-search-vertical-slice.md), à ne pas interpréter comme un nouveau run à 121 tests |
| Recherche technique | Tranche de 50 VTT livrée |
| Échantillon | Non représentatif |
| Pertinence éditoriale | `UNKNOWN` : aucun jugement humain concluant n'est enregistré |

Cette mise à jour ne rejoue pas la baseline. Les résultats post-lot appartiennent au suivi du coordinateur et doivent nommer leur SHA, commande et sortie.

## 2. Contrat de données

Le pipeline actuel récupère les **sous-titres VTT YouTube**. Il ne traite pas l'audio.

| Support | Rôle exact | Autorité |
|---|---|---|
| VTT YouTube | Texte et timestamps récupérés | Source de vérité |
| `catalog.sqlite3` | Inventaire, import et repli de récupération vidéo | Base opérationnelle indépendante, jamais une source de passages |
| `search-v1.sqlite3` | Index dérivé des passages et timestamps VTT | Reconstructible à tout moment ; sa promotion full-corpus comme référence produit dépend de la gate P2 |

`search-v1.sqlite3` dérive directement des VTT. Il ne dérive pas de `catalog.sqlite3`.

## 3. Phases : exécution distincte de la promotion

| Ordre | Phase | Construction et tests | Promotion/readiness |
|---:|---|---|---|
| P0 | Documentation de vérité | `TERMINÉE` | Aucune gate humaine déclarée passée |
| P1 | Resolver Ollama | `TERMINÉE`, tests et revue passés | Génération LLM réelle non rejouée dans ce lot |
| P2 | Recherche utile et performance, 50 VTT | `TERMINÉE TECHNIQUEMENT` | `UNKNOWN` jusqu'au jugement humain lié à l'artefact P2 |
| P3 | Corpus complet | `TERMINÉE TECHNIQUEMENT` | Référence produit full-corpus bloquée par P2 `UNKNOWN` |
| P4 | MCP minimal | `TERMINÉE TECHNIQUEMENT` | Exposition/installation comme accès produit prêt bloquée par P2 `UNKNOWN` |
| P5 | Installation | `TERMINÉE TECHNIQUEMENT` | Recommandation ou promotion d'usage produit bloquée par P2 `UNKNOWN` |

L'ordonnancement P3 → P4 → P5 reste technique : P4 consomme le contrat stable de P3 et P5 vérifie P4. Il ne dépend pas d'une promotion éditoriale de P2.

## 4. P2 : contrat d'évidence obligatoire avant promotion

L'[artefact P2](evidence/2026-08-28-p2-50-vtt-evaluation.md) est suivi dans le repo, mais son évaluation reste non remplie. P2-S1 le complète avant le handoff à P2-S2. Avant l'ajout de jugements, son statut est `UNKNOWN`, pas `PASS`.

L'artefact contient obligatoirement :

1. le snapshot Git, branche, worktree et producteur ;
2. le manifeste **ordonné** des 50 VTT : identité vidéo, langue, chemin relatif et hash source ;
3. le hash du manifeste complet et la méthode de calcul ;
4. les sujets et requêtes ;
5. les résultats de recherche, passages et timestamps évalués ;
6. les jugements humains, leur auteur et leur date, ou l'absence explicite de jugement ;
7. le seuil adopté et le statut final `PASS`, `FAIL` ou `UNKNOWN`.

P2-S2 consomme exactement la version fusionnée de cet artefact, puis en devient l'unique propriétaire pour y ajouter les jugements. Un manifeste différent, un hash absent ou un jugement absent maintient `UNKNOWN`. Cela bloque uniquement promotion, exposition et readiness produit.

## 5. P3 à P5 : implémentation autorisée sous réserve de promotion

### P3 : Corpus complet

Construire et tester `search-v1.sqlite3` depuis les VTT, réconcilier les comptes et conserver une génération précédente en cas d'échec. `catalog.sqlite3` conserve son rôle indépendant. Le résultat peut être testé, mais ne devient pas la référence produit full-corpus tant que P2 reste `UNKNOWN`.

Résultat local attesté : 3 270 documents, 183 789 passages, base de 528 MiB,
build en 48,75 s, p95 chaud de 13,806 ms et recherche dans un nouveau
processus en 0,32 s avec cache disque non purgé. Voir la
[preuve full-corpus](evidence/2026-08-28-full-corpus-benchmark.md).

### P4 : MCP minimal

Construire et tester localement un MCP stdio read-only limité à `list_corpora`, `search_videos`, `search_passages` et `get_passage`. Il appelle les mêmes couches catalogue et recherche que la CLI et n'expose ni écriture, ni SQL brut, ni shell, ni URL arbitraire. Son exposition comme surface produit reste bloquée par P2 `UNKNOWN`.

### P5 : Installation

Écrire puis rejouer une installation client locale comme test d'intégration des quatre outils. Ne pas présenter cette installation comme prête pour les usages produit ou comme recommandée tant que P2 est `UNKNOWN`.

## 6. Packs, exports et extensions

Packs et exports commencent seulement à la demande d'un article réel, avec son angle et son besoin de passages sourcés. UI, extension navigateur, produit hébergé, embeddings, graphe et Qdrant restent conditionnels selon les déclencheurs mesurés définis dans la [roadmap](../ROADMAP.md).

## 7. Parallélisation et fin de trajectoire

Les propriétaires, l'ordre de fusion P2 et la séparation construction/promotion sont définis dans les [sessions parallèles](PARALLEL-SESSIONS.md).

La construction technique P3 à P5 peut se terminer avant P2. La trajectoire n'est prête à être promue comme produit que lorsque P2 contient une décision humaine explicite et que les tests de P3 à P5 sont passés sur l'artefact et le code concernés.
