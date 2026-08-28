# Sessions parallèles : suivi d'exécution

**Mise à jour :** 2026-08-28
**État du lot actif :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**Règle :** `UNKNOWN` bloque promotion/readiness produit, pas l'implémentation P3 à P5 autorisée.

**Références :** [Plan consolidé V2](2026-08-27-CONSOLIDATED-v2.md) et [roadmap](../ROADMAP.md).

## Règles communes

1. Chaque session utilise un worktree isolé et ne modifie que ses fichiers attribués.
2. Les changements existants du checkout principal restent hors périmètre.
3. Utiliser des pathspecs explicites, jamais `git add .` ni `git add -A`.
4. Les VTT YouTube sont la source de vérité ; le pipeline récupère des sous-titres et ne traite pas l'audio.
5. Une gate humaine absente ou un artefact P2 incomplet vaut `UNKNOWN`.
6. `UNKNOWN` interdit la promotion, l'exposition et la recommandation produit. Il n'interdit pas le développement ou les tests locaux explicitement autorisés.

## Dépendances

```text
P0 documentation [terminée]
             ↓
P1 resolver Ollama [terminé]
             ↓
P2 recherche technique [terminée] ; jugement humain [UNKNOWN]
             ↓ décision humaine : promotion seulement

P3 corpus complet [terminé] -> P4 MCP 2 outils [terminé] -> P5 installation [terminée]
```

P3 à P5 suivent leurs dépendances techniques, non une autorisation éditoriale. P2 décide uniquement si leurs résultats peuvent être promus comme produit prêt.

## P0 : Documentation de vérité

**État :** `TERMINÉE`.

**Propriété :** `ROADMAP.md`, `plans/2026-08-27-CONSOLIDATED-v2.md`, `plans/README.md`, `plans/PARALLEL-SESSIONS.md`.

## P1 : Resolver Ollama

**État :** `TERMINÉ`.

**Une seule session.** Elle possède le resolver et ses tests. Sa sortie est un correctif ciblé et une preuve de comportement ou `UNKNOWN` si l'environnement ne permet pas le test.

## P2 : Recherche utile et performance sur 50 VTT

**État technique :** `TERMINÉ`. **Promotion :** `UNKNOWN` sans jugement humain.

### Artefact et propriété

L'[artefact P2](evidence/2026-08-28-p2-50-vtt-evaluation.md) est suivi dans le repo, mais son évaluation reste non remplie. P2-S1 le complète et en devient l'unique propriétaire jusqu'à sa fusion. L'artefact contient :

- snapshot Git, branche, worktree et producteur ;
- manifeste **ordonné** des 50 VTT : vidéo, langue, chemin relatif et hash ;
- hash et méthode de calcul du manifeste ;
- sujets, requêtes, résultats, passages et timestamps évalués ;
- seuil ;
- jugements humains ou absence explicitement déclarée ;
- statut final `PASS`, `FAIL` ou `UNKNOWN`.

P2-S1 ne crée aucun faux jugement. Avant revue humaine, l'artefact porte `UNKNOWN`.

### Sessions, branches et ordre de fusion

| Ordre | Session et branche | Propriété exclusive | Sortie |
|---:|---|---|---|
| 1 | P2-S1, `codex/p2-s1-50-vtt` | Artefact P2 jusqu'à fusion, sélection, recherche, mesures de latence et entrées invalides | Manifeste traçable et résultats reproductibles |
| 2 | P2-S2, `codex/p2-s2-editorial-evaluation` | Artefact P2 après fusion S1, cas d'articles, protocole et jugements | Jugements attachés au même manifeste et statut final |

P2-S2 démarre après fusion de P2-S1 et consomme l'identifiant de commit ainsi que le hash du manifeste enregistrés dans l'artefact. Si l'un manque, elle rend `UNKNOWN`. Les branches fusionnent strictement S1, puis S2. Aucun autre échantillon ne peut servir à la promotion.

**Gate de promotion :** un jugement humain explicite, lié au manifeste exact, est requis. `UNKNOWN` ou `FAIL` bloque promotion/readiness, mais pas P3 à P5.

## P3 à P5 : développement terminé, promotion bloquée si P2 est `UNKNOWN`

| Phase | Construction/tests autorisés | Dépendance technique | Promotion ou exposition interdite tant que P2 est `UNKNOWN` |
|---|---|---|---|
| P3 corpus complet | Construire et tester `search-v1.sqlite3` depuis les VTT, avec réconciliation | Contrat de passages stable | Le promouvoir comme référence produit full-corpus |
| P4 MCP minimal | Construire/tester localement `search_passages` et `get_passage` | P3 et contrat de recherche stables | Le présenter comme accès produit prêt ou le distribuer pour usage produit |
| P5 installation | Rejouer une installation client locale comme test d'intégration | P4 testé | La recommander ou la promouvoir comme installation produit prête |

`catalog.sqlite3` reste une branche d'inventaire/import/repli vidéo. Il ne précède ni n'alimente `search-v1.sqlite3`, qui est dérivé directement des VTT.

## Chantiers sans session active

| Chantier | Déclencheur |
|---|---|
| Packs et exports | Demande d'un article réel avec angle et besoin de passages sourcés |
| UI locale | Friction répétée mesurée dans des usages réels |
| Extension navigateur | Besoin récurrent non couvert par CLI/MCP local |
| Produit hébergé | Besoin de partage ou accès distant, avec décision sécurité/exploitation |
| Embeddings/hybride | Échec lexical mesuré sur des requêtes réelles |
| Graphe | Questions multi-hop nommées non résolues par FTS5 et filtres |
| Qdrant | Embeddings adoptés et budget SQLite échoué après profilage |

## Handoff obligatoire

```text
Phase, branche et commit :
Fichiers possédés modifiés :
Commande, environnement, SHA et sortie de vérification :
Évidence fraîche ou absence d'évidence :
Statut PASS, FAIL ou UNKNOWN :
Promotion/readiness autorisée ou bloquée :
Changement demandé sur fichier partagé :
Rollback testé ou non applicable :
```
