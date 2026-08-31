# Sessions parallèles : suivi d'exécution

**Mise à jour :** 2026-08-31

**Branche d'intégration :** workflow cumulatif local

**Activation globale :** `false`

## Règles

1. Chaque lot utilise un worktree isolé et des pathspecs explicites.
2. Les modifications utilisateur du checkout principal restent hors périmètre.
3. Les contrats partagés sont intégrés avant les adaptateurs qui les consomment.
4. Chaque lot reçoit une revue fraîche avant intégration.
5. Un statut `UNKNOWN` reste `UNKNOWN` et bloque les claims correspondants.
6. Aucun lot ne modifie une configuration globale sans transaction par digest.

Le fichier utilisateur `CLAUDE.md` du checkout principal n'appartient pas au lot
documentaire et n'est pas modifié.

## Graphe livré

```text
Task 0 gates
Task 1 models
  ├── Task 2 store
  ├── Task 3 assessment
  └── Task 4 discovery
         ↓
Task 5 CLI foundation
         ↓
Task 6 candidate decisions
  ├── Task 7 acquisition and retry
  └── Task 8 dossier export
         ↓
Task 9 Claude Code and Codex assets
         ↓
Task 10 E2E, quality and docs
```

## État des lots

| Task | Propriété | État fonctionnel |
|---:|---|---|
| 0 | Artefacts de gates | Terminé |
| 1 | Modèles, paths, config | Terminé |
| 2 | Store SQLite de recherche | Terminé |
| 3 | Évaluation locale | Terminé |
| 4 | Découverte bornée | Terminé |
| 5 | Start, status, decide | Terminé |
| 6 | Discover, candidates, approve, cancel | Terminé |
| 7A | Acquisition structurée et publication par paire | Terminé |
| 7B | Workflow acquire et retry | Terminé |
| 8A | Dossier déterministe | Terminé |
| 8B | Export CLI et sécurité de racine | Terminé |
| 9A | Quatrième skill et setup assets-only | Terminé dans le dépôt |
| 9B | Prompts anglais et wheel installable | Terminé dans le dépôt |
| 10A | E2E et limites hostiles | En intégration |
| 10B | Documentation | En cours dans ce worktree |
| 10C | Qualité finale | En intégration |

## Contrats de fusion

| Surface | Propriétaire final | Gate |
|---|---|---|
| `research/models.py` | Coordinateur | Valeurs et JSON stables |
| `research/store.py` | Store | Révisions, transitions et idempotence |
| `research/assessment.py` | Assessment | No-network et snapshot cohérent |
| `research/discovery.py` | Discovery | Maximum 10, aucun write source |
| `research/acquisition.py` | Acquisition | 1 à 5 IDs exacts, résultats structurés |
| `research/dossier.py` | Dossier | Déterminisme, confinement et séparation source |
| `cli_research.py` | Coordinateur | Dix commandes et erreurs bornées |
| Assistant assets | Task 9 | Quatre skills, prompts anglais, MCP read-only |
| Docs | Task 10B | Aucun claim supérieur aux preuves |

## Gates externes

| Gate | Statut |
|---|---|
| Pertinence humaine | `UNKNOWN` |
| Découverte sur 3 sujets | `PASS`, 10 candidats par sujet |
| Refresh complet | `PASS`, 5 builds, p95 `47.122951 s` |
| Taille refresh | 3 332 documents, 184 636 passages |
| YouTube live final | `UNKNOWN` |
| Claude Code frais | `UNKNOWN` |
| Codex frais | `UNKNOWN` |
| Activation globale | `false` |
| GitHub CI | Absente |

La pertinence `UNKNOWN` n'empêche pas une acquisition locale explicitement
approuvée. Elle interdit un claim de qualité validée et toute activation
globale.

## Après intégration locale

Ordre strict :

1. publier les résultats fonctionnels et de qualité au SHA final;
2. réaliser les 20 jugements humains de pertinence;
3. lancer les canaris frais Claude Code et Codex;
4. seulement ensuite préparer un candidat global inerte et son digest;
5. conserver web, extension, writable MCP, vectoriel et graphe hors du chemin
   critique tant que leurs déclencheurs ne sont pas observés.

## Handoff obligatoire

```text
Task, branche et commit:
Fichiers possédés:
Tests et checks réellement exécutés:
Gates PASS:
Gates FAIL:
Gates UNKNOWN:
Effets externes ou absence d'effets:
Rollback ou non applicable:
```
