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

Le candidat global comprend deux commits inertes, `1f79d23` et `62aa9ca`. Ils
durcissent les remplacements de symlinks, vérifient les snapshots avant
restauration et rendent le rollback reprenable après interruption. La suite
complète passe avec 144 tests sur 144, dont 24 tests installateur. La revue
adverse finale ne remonte aucun blocker.

Le rendu a été répété dix fois à partir du vrai commit complet
`62aa9ca053c9bc7c03564ffb08864d5d02f8f8b6`. Les dix exécutions produisent la
même release
`60cbcac1db3728e861560cd945e614bca0b8b0e8404acadddc8d57e1b46be1eb`,
et `scripts/check.mjs` retourne `issues: []`.

La source globale live reste à
`e760a8116310c793ebef40318072a5eab777b3ba`. Le bundle Git exact, son hash et le
manifeste d'approbation sont conservés dans
[`global-candidate-62aa9ca`](global-candidate-62aa9ca/approval-source-candidate.json).
Le digest utilise une sérialisation JSON récursive avec clés triées, puis
SHA-256. L'application prévue vérifie le bundle, récupère sa ref et exige un
fast-forward exact vers `62aa9ca`.
L'approbation d'intégration attendue est:

```text
GO APPLY YT GLOBAL CANDIDATE 3e6146fb1740d63593d64c65b317248d4638e14de0735d0afe6b09fc4d68d6eb
```

La transaction partagée a aussi été préparée sans mutation globale à partir
des préimages live et d'un index privé de 287 projets. Elle couvre huit
opérations. Les fichiers `CLAUDE.md` et `AGENTS.md` restent byte-identiques.
L'approbation d'activation attendue, distincte de l'intégration du code, est:

```text
GO INSTALL SHARED cbd58f0a09d95e9ba676b1f9271f9fec9c2966ac2ef8dc9223d45167ef52f296
```

Ces deux approbations sont sensibles au drift. Une modification du HEAD global,
du bundle ou d'une préimage live impose de reconstruire les digests.

Deux limites restent documentées. Un déplacement de snapshot entre volumes
peut échouer avec `EXDEV`, sans mutation destructive. Les anciens journaux de
schéma 1 dépourvus de `rollbackPostimage` et `restored` ne sont pas compatibles
avec la reprise renforcée.

## Gates encore ouvertes

1. Donner un verdict humain `PASS` ou `FAIL` aux requêtes P2 sur un article réel.
2. Exécuter un canari court avec un modèle Ollama installé.
3. Exécuter un canari court avec un modèle MLX installé.
4. Approuver ou refuser l'intégration du candidat global avec son digest exact.
5. Après intégration et nouvelle vérification, approuver ou refuser la transaction partagée avec son digest distinct.
6. N'ouvrir le chantier hébergé ou extension que lorsqu'un déclencheur d'usage du plan H est observé.
