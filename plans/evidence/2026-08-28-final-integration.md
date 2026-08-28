# Preuve d'intégration locale du 28 août 2026

## Verdict

Le candidat local réunit les backends explicites, le catalogue portable v2,
les skills et agents natifs, ainsi que le retrait des collisions avec les cinq
commandes Claude historiques. La suite Python, le packaging et le candidat
full-corpus passent leurs gates techniques. Aucun corpus actif, dépôt distant
ou environnement global Claude Code/Codex n'a été modifié.

La pertinence éditoriale reste `UNKNOWN`. Le routeur implicite est rejeté pour
ce lot. L'invocation explicite des skills est le chemin supporté.

## Provenance du code

| Lot | Commits conservés | Verdict |
| --- | --- | --- |
| Commandes historiques explicites uniquement | `826f243`, suivi `e694b2a` | PASS |
| Backends nommés | `69142f6`, `aea70f7` | PASS |
| Catalogue portable et lecteur immuable | `83a5a17`, `5ea4945`, `78baf77`, `1497a43` | PASS |
| Publication copy-on-write atomique | `90172eb` | PASS après deux revues correctives |

Le candidat d'intégration avant documentation est `087ed57`. Les documents de
ce commit de clôture ne changent pas le runtime testé.

## Validation du code et du paquet

| Contrôle | Résultat |
| --- | --- |
| Suite Python sur l'intégration | `508 passed, 10 subtests passed` |
| Build offline | wheel et sdist 0.2.0 |
| Installation wheel avec MCP | 37 paquets compatibles |
| Smoke CLI | doctor, acquisition dry-run, export, catalogue, index et recherche |
| Smoke MCP | quatre outils et entrypoint stdio |
| Catalogue installé | schéma v2, quick check, clés étrangères, mode 0600 |
| Lectures catalogue | aucun lock, stage, journal, WAL ou SHM créé |

Ces contrôles n'ont envoyé aucun appel réel à YouTube, Ollama, MLX ou un
fournisseur cloud. Les canaris de génération restent distincts.

## Candidat full-corpus temporaire

Le candidat a été construit sous
`/private/tmp/yt-insights-b3.ITkscC/candidate`, jamais sous `output/`.

| Mesure | Résultat |
| --- | --- |
| Fichiers du corpus avant et après | 10 240 |
| SHA-256 des manifests avant et après | `1131a6422dfafc4646f2e2b9c35f21a88cab2ab5fe5e04223731a541571103ba` |
| Catalogue | 3 130 vidéos, 3 132 sources, 6 487 artefacts, 5 erreurs persistées |
| Index FTS | 3 270 documents, 183 789 passages, 0 source invalide |
| SHA-256 catalogue | `dd5407f367f556d8ea085899e4af2ceca867caaf94f93fc4cd8f0813ebd046b5` |
| SHA-256 index FTS | `31af29104623efeff1f2143bcca57ad8e598ea4be8c1a1452870a818fe59e31c` |
| SHA-256 receipt FTS | `43562c514e232b4347ef76f1ceef6c5933f23e1261b46a81ee7a0ec5587db327` |

Les chemins stockés sont relatifs et portables. Les lectures depuis un second
répertoire courant ont laissé les cinq fichiers du candidat strictement
inchangés. Les cinq erreurs d'import restent visibles et expliquent le statut
catalogue `partial`.

## Évaluation du routeur implicite

Le holdout final contient 45 prompts sans chevauchement normalisé avec le jeu
d'entraînement. Le meilleur principe simple, l'exclusivité top-1, obtient
30/30 positifs et 0 confusion inter-skill, mais active encore 5/15 requêtes
interdites. Les seuils par skill, marges et ancres d'intention échouent aussi à
au moins un gate. Aucun commit expérimental n'est promu.

## Candidat global inerte

Le correctif `44aee7d` traite le cas où la racine de skills Claude est absente
et conserve une cible créée en concurrence. Il passe 112 tests, dont 19 tests
installateur. Le rendu inerte `73f4393aaa907a2b85638dc032646f5661ea8feb0124210ef95a81e4f32bd11f`
retourne `issues: []`.

La source globale live reste à `4d8a5a4`. Son installation nécessite encore un
diff exact, un digest lié aux préimages live et une approbation explicite.

## Gates encore ouvertes

1. Donner un verdict humain `PASS` ou `FAIL` aux requêtes P2 sur un article réel.
2. Exécuter un canari court avec un modèle Ollama installé.
3. Exécuter un canari court avec un modèle MLX installé.
4. Approuver ou refuser le candidat global après revue de son diff et de son digest.
5. N'ouvrir le chantier hébergé ou extension que lorsqu'un déclencheur d'usage du plan H est observé.
