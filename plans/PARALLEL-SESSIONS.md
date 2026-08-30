# Sessions parallèles : suivi d'exécution

**Mise à jour :** 2026-08-30
**État du socle livré :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**État du runtime et des assets agentiques projet :** `IMPLÉMENTÉ ET VALIDÉ LOCALEMENT`.
**État de la source et de la release partagée :** `INTÉGRÉES ET ACTIVES`.
**État du routeur réel :** `REJETÉ`, aucun candidat disjoint ne passe tous les gates.
**État de l'installation globale :** `PRÊTE À APPLIQUER`, setup local livré; runtime, agents et MCP live restent à installer.
**Règle :** `UNKNOWN` bloque promotion/readiness produit, pas l'implémentation P3 à P5 autorisée.

**Références :** [architecture agentique](specs/AGENT-PLATFORM.md), [runtime](2026-08-28-09-agent-ready-runtime.md), [intégration globale](2026-08-28-10-claude-codex-global-integration.md), [plan consolidé V2](2026-08-27-CONSOLIDATED-v2.md) et [roadmap](../ROADMAP.md).

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

P3 corpus complet [terminé] -> P4 MCP 4 outils [terminé] -> P5 installation locale [terminée]

A0 contrats runtime -> A1 chemins
                         ↓
              ┌──────────┼──────────┐
              A2 doctor  A3 acquire A4 export
              └──────────┼──────────┘
                         ↓ A5 intégration CLI
             contrats runtime gelés
    ┌────────┼─────────────┐
    B1 MCP   B2 skills     B3 agents + évaluation du routage
    └────────┼─────────────┘
             ↓ candidat local validé
        C1 source + shared [terminés] -> runtime + integrations [à approuver] -> C3 sessions neuves

D1 backends explicites + MLX : terminé, canaris réels séparés

H service hébergé + extension : conditionnel aux signaux d'usage, hors chemin critique A-C
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
| P4 MCP minimal | Construire/tester localement `list_corpora`, `search_videos`, `search_passages` et `get_passage` | P3, catalogue et contrat de recherche stables | Le présenter comme accès produit prêt ou le distribuer pour usage produit |
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

## Nouveau lot A à C : exécution parallèle Claude Code et Codex

Le lot démarre sur des worktrees isolés. Aucun agent ne possède les fichiers
utilisateur déjà modifiés dans le checkout principal : `.claude/skills/yt-add-channel.md`,
`CLAUDE.md`, `runbook/run-channel.sh`, `batches/` et `scripts/build_speakers.py`.

### Vague A : rendre le runtime appelable partout

| Session | Propriété exclusive | Dépendance | Gate de fusion |
|---|---|---|---|
| A1 `codex/agent-paths` | `config.py`, nouveau `paths.py`, adaptateurs de chemins et tests associés | `TERMINÉ` | Priorité CLI > env > TOML > défaut, même corpus depuis deux cwd |
| A2 `codex/agent-doctor` | `doctor.py`, `cli_doctor.py` et tests associés | `TERMINÉ` | Aucune valeur de secret affichée |
| A3 `codex/agent-acquire` | façade `acquire`, preview, downloader et tests associés | `TERMINÉ` | vidéo unitaire immédiate, channel/playlist bloqués sans `--yes`, aucun cookie automatique |
| A4 `codex/agent-export` | exporteur, formats VTT/TXT/Markdown et tests associés | `TERMINÉ` | export déterministe, sourcé, sans appel LLM |
| A5 coordinateur | `cli.py` et test d'intégration des commandes | `TERMINÉ` | Quatorze noms de commande stables et tests de comportement, dont `setup` |

Le coordinateur possède les contrats partagés et `cli.py`. Les sessions
proposent leurs ajouts sous forme de fonctions isolées. Le coordinateur intègre
les sous-commandes dans `cli.py` pour éviter les conflits de fusion.

### Vague B : exposer le runtime aux deux hôtes

Cette vague démarre après fusion de A5. B1 dépend aussi de l'index
existant, mais pas des backends LLM.

| Session | Propriété exclusive | Peut démarrer en parallèle avec | Gate de fusion |
|---|---|---|---|
| B1 `codex/agent-mcp-four-tools` | serveur MCP, quatre contrats read-only et tests | `TERMINÉ` | exactement quatre outils, chemins absolus, aucune mutation |
| B2 `codex/agent-portable-skills` | trois dossiers `.agents/skills/youtube-*` et fixtures de skills | `TERMINÉ` | mêmes commandes sur Claude Code et Codex, aucune dépendance au cwd |
| B3 `codex/agent-native-adapters` | agent Claude, agent Codex et corpus 45 prompts | `TERMINÉ AVEC REJET DU ROUTEUR`: assets livrés; invocation explicite retenue après 30/30 positifs mais 5/15 activations interdites | Aucun hook implicite installé |
| B4 `codex/agent-packaging-docs` | smoke wheel et docs repo | `TERMINÉ` | installation minimale et MCP hors checkout, assets assistants présents dans le wheel |
| B5 `codex/assistant-setup` | setup transactionnel, tests clients factices et prompts | `TERMINÉ LOCALEMENT` | dry-run sans écriture, conflit fail-closed, rollback et verify au vert |

### Vague C : installation globale partiellement terminée

La source globale et la release partagée ont été appliquées par deux transactions
approuvées et journalisées. Le runtime, les agents natifs et les entrées MCP restent
des transactions séparées. Ils ne sont pas installés à ce checkpoint.

| Session | Propriété exclusive | État et gate |
|---|---|---|
| C1 source globale | source de release `~/.config/ai-agents`, tests inertes, manifestes et diffs expurgés | `TERMINÉ`: source `62aa9ca...`, 144 tests et bundle validés |
| C2 release partagée | index actif et trois skills portables | `TERMINÉ`: release `60cbcac...`, 8 opérations journalisées, canari Codex positif |
| C2 runtime | wheel, config runtime et binaires gérés | `À REPRÉPARER`: aucun runtime global `uv` installé; nouvelle préimage et nouveau digest requis |
| C2 intégrations | agents natifs Claude/Codex et entrées MCP | `PRÊT À APPLIQUER`: commande versionnée; préimages live et approbation digest encore requises |
| C3 validation | sessions Claude Code et Codex neuves, canaris de refus d'écriture | `PARTIEL`: skills visibles dans Codex; Claude, agents natifs, MCP global et parité multi-cwd restent `UNKNOWN` |

La configuration globale n'installe pas un second hook YouTube. Le routeur
global existant n'est ajusté que si le corpus de 45 prompts prouve un manque.
Après réussite du corpus, le hook Claude local est retiré de
`.claude/settings.json` dans une modification dédiée. Son script reste conservé
pour rollback jusqu'à validation des sessions neuves.

### Lot D : optimisation LLM non bloquante

D1 répare la sélection MLX et explicite Ollama, MLX, cc-bridge et remote. Il
démarre après A1 et ne possède jamais `config.py` en parallèle avec A1. Son
statut, y compris un canari MLX `UNKNOWN`, ne bloque pas A3 à C3.

### Charge indicative du lot local et agentique

| Lot | Charge | Durée murale avec trois sessions |
|---|---:|---:|
| A0 contrats et intégration CLI | 0,5 à 1 jour | 0,5 à 1 jour |
| A1 à A4 runtime | 4 à 7 jours cumulés | 1,5 à 3 jours |
| B1 à B4 MCP, skills, agents et packaging | 3,5 à 6 jours cumulés | 2 à 3 jours |
| C1 candidat global | 2 à 3 jours | 2 à 3 jours |
| C2 et C3 installation et validation | 0,5 à 1 jour | 0,5 à 1 jour, hors attente d'approbation |
| Total | 10,5 à 18 jours cumulés | 6,5 à 11 jours |

Ces fourchettes couvrent code, tests, revue et documentation. Elles excluent le
lot hébergé, les appels réseau longs et le temps d'attente entre présentation
des digests et approbation globale.

### Lot H : hébergement conditionnel

Le [plan hébergé et extension](2026-08-28-11-hosted-extension.md) reste hors du
chemin critique. H1 menace/contrats et H4 maquettes d'extension peuvent avancer
en parallèle après activation. API, worker et extension ne démarrent pas avant
un signal d'usage consigné et un schéma d'API gelé.

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
