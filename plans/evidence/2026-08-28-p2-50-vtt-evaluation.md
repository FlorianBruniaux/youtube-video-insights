# Évidence P2 : protocole d'évaluation de la recherche

**Version du contrat :** `2.0`

**Statut du fichier :** `UNKNOWN`

**Statut du pilote :** `UNKNOWN`

**Statut de la release :** `UNKNOWN`

Ce document définit comment produire, juger et rejouer l'évidence de pertinence demandée par [l'issue #12](https://github.com/FlorianBruniaux/youtube-video-insights/issues/12) et son [plan d'évaluation](../2026-08-27-03-evaluation-observability.md). Il ne contient aucun sujet réel, aucun résultat observé et aucun jugement humain. Son existence ne prouve ni la pertinence de la recherche ni son aptitude à une release.

## Deux gates, deux dénominateurs

| Gate | Corpus | Unité évaluée | Volume requis | Critère de décision |
|---|---|---|---:|---|
| Pilote historique Phase 1B | index représentatif de exactement 50 VTT | résultat classé | exactement 20 jugements, soit 4 requêtes × 5 premiers résultats, couvrant au moins 3 sujets réels | `PASS` si au moins 16 résultats sur 20 ont une pertinence humaine de `1` ou `2` |
| Gate complet de l'issue #12 | index de release identifié par son hash et ses compteurs | cas de requête avec oracle humain | 60 à 100 cas uniques | toutes les métriques et tous les gates requis sont calculés sur le même packet revu |

Un `PASS` du pilote autorise seulement la poursuite de l'évaluation. Il ne vaut jamais `PASS` de release. Les 20 jugements du pilote sont des jugements de résultats. Les 60 à 100 éléments du gate complet sont des cas de requête, chacun lié à un oracle humain. Additionner des hits pour prétendre atteindre 60 cas est interdit.

Statut actuel des deux gates : `UNKNOWN`.

## Entrées obligatoires

Avant toute préparation, l'opérateur doit disposer de :

1. un commit Git complet de 40 caractères correspondant au code évalué ;
2. un index SQLite FTS5 fini, en lecture seule pendant la préparation ;
3. le hash SHA-256 de cet index ;
4. un fichier de requêtes sans placeholder, dérivé de `plans/evidence/2026-08-30-p2-query-template.json` ;
5. pour le pilote, quatre requêtes réelles rattachées à au moins trois sujets d'article réels ;
6. pour la release, 60 à 100 cas uniques couvrant `exact`, `natural_question`, `paraphrase`, `bilingual`, `filter`, `hostile` et `no_answer` ;
7. une identité de reviewer humain et une date ISO 8601 avec fuseau, saisies seulement lors de la revue.

Pour le pilote, `documents_indexed` doit valoir exactement `50`. Le script
enregistre ce compteur mais ne décide pas à la place de l'opérateur si la slice
est représentative. Une autre valeur maintient le pilote à `UNKNOWN`.

Les seuls filtres admis dans le fichier de requêtes sont `channel` et `language`, chacun sous forme de chaîne non vide ou `null`. Les prompts, labels, requêtes et consignes opérationnelles ajoutés au packet doivent être en anglais.

## Préparer le packet déterministe

Le script versionné `scripts/prepare_search_relevance_evaluation.py` doit être exécuté depuis la racine du dépôt :

```bash
uv run python scripts/prepare_search_relevance_evaluation.py \
  --database /ABSOLUTE/PATH/TO/search-v1.sqlite3 \
  --queries-file /ABSOLUTE/PATH/TO/reviewed-queries.json \
  --output /ABSOLUTE/PATH/TO/p2-evaluation-packet.json \
  --commit-sha FULL_40_CHARACTER_GIT_COMMIT \
  --top-k 10
```

`--top-k 10` est requis pour préparer en une seule passe les données nécessaires à MRR@10 et nDCG@10. Le pilote ne juge que les rangs 1 à 5 des quatre cas pilote. Le script ne crée jamais de jugement humain : chaque champ de jugement doit rester `null` dans le packet préparé.

Le script doit échouer avec un code non nul si le commit n'est pas un SHA complet, si le JSON est invalide, si un placeholder subsiste, si un identifiant ou un contrat requête-filtres est dupliqué, si un filtre est inconnu, si `top-k` sort de l'intervalle supporté, si l'index est absent ou invalide, ou si le fichier de sortie existe sans `--force`. Le script vérifie la forme du SHA déclaré, pas son existence dans le dépôt ni son égalité avec `HEAD`. L'opérateur doit effectuer ces deux contrôles avant l'exécution.

`--force` est réservé au remplacement explicite d'un packet non revu après archivage de son hash. Il ne doit jamais écraser une revue humaine ou servir à masquer un changement d'entrée. Le script ne peut pas distinguer un packet préparé d'une copie revue existante : cette protection reste une responsabilité de l'opérateur.

## Contenu probant du packet

Pour être recevable, le packet préparé doit conserver dans un ordre canonique :

- la version de schéma et le statut `UNKNOWN` ;
- le SHA Git complet déclaré avec `--commit-sha` ;
- le SHA-256 des fichiers sources des modules réellement chargés et observés,
  ainsi que leur hash agrégé, avec des noms logiques sans chemin absolu ;
- le SHA-256 des octets du fichier de requêtes ;
- le SHA-256 et la taille de l'index SQLite, ainsi que ses compteurs de sources, documents et passages ;
- la valeur de `top-k` ;
- les cas dans l'ordre du fichier d'entrée ;
- pour chaque cas, son identifiant, son sujet, son label, sa requête, sa catégorie, sa langue, ses filtres et les résultats classés ;
- pour chaque résultat, le rang, `passage_id`, `document_id`, `source_relpath`, `source_sha256`, identité de chaîne et vidéo, langue, ordinal, timestamps, texte, extrait et URL YouTube ;
- des champs de revue initialisés à `null`, jamais à une valeur générée.

Le script ouvre l'index avec un descripteur en lecture seule sans suivre de lien,
le copie vers un répertoire temporaire privé, copie et valide son receipt, puis
utilise uniquement ce snapshot pour les compteurs et toutes les requêtes. Un
remplacement pendant la capture échoue. Un remplacement après la capture ne
modifie pas le packet : celui-ci reste lié au hash du snapshot capturé et ne
prétend pas décrire le nouvel index actif.

Le hash SHA-256 externe du packet préparé lie ensemble le commit déclaré, les
hashes des sources chargées observées, le hash du snapshot d'index, le hash des
requêtes et les résultats. Ces hashes attestent les fichiers sources observés,
pas le bytecode déjà exécuté. Le script ne stocke pas le hash du packet à
l'intérieur de lui-même.
Aucun timestamp courant, chemin de worktree ou autre valeur volatile n'entre
dans sa sérialisation. À entrées identiques, deux préparations doivent produire
des octets et un hash identiques.

L'auteur et la date de préparation sont consignés dans le registre externe avec le hash du packet, pas dans le packet préparé canonique. Le reviewer et la date de revue entrent ensuite dans la copie revue, qui reçoit son propre hash. Cette séparation préserve à la fois le déterminisme de la préparation et l'attribution humaine.

Après préparation, le reviewer travaille sur une copie immuable référencée par ce hash. Il renseigne :

- `evaluation.reviewer` avec une personne identifiable ;
- `evaluation.reviewed_at` au format ISO 8601 avec fuseau, ainsi que `evaluation.method`, `evaluation.decision` et, si nécessaire, `evaluation.notes` ;
- pour chaque résultat, `judgment.relevance`, `judgment.reviewer`, `judgment.reviewed_at` et, si nécessaire, `judgment.notes` ;
- pour chaque cas `no_answer`, la confirmation humaine qu'aucun document du corpus n'est pertinent ;
- le hash du packet préparé et le hash du packet revu.

`0` signifie non pertinent, `1` utile mais partiel, `2` directement pertinent. Pour Recall@5 et MRR@10, les grades `1` et `2` sont pertinents. nDCG@10 conserve les grades `0`, `1`, `2`.

Le packet préparé contient les résultats à juger, mais il ne construit pas un oracle exhaustif des documents pertinents. Pour le gate complet, le reviewer doit donc ajouter à chaque cas une liste `oracle_documents` issue d'une recherche humaine du corpus, avec `document_id`, grade et provenance de vérification. Un cas `no_answer` porte une liste vide et une confirmation humaine explicite. Sans cet oracle indépendant, Recall@5 est `UNKNOWN` : juger seulement les résultats retrouvés rend son dénominateur inconnu.

## Calculs et règles de statut

Les métriques utilisent uniquement le packet revu, sans modifier les résultats classés :

- `Recall@5` : nombre de documents pertinents retrouvés dans les cinq premiers rangs, divisé par le nombre de documents pertinents de l'oracle pour le cas, puis moyenne sur les cas répondables ;
- `MRR@10` : inverse du rang du premier document pertinent jusqu'au rang 10, ou `0` si aucun n'est retrouvé, puis moyenne sur les cas répondables ;
- `nDCG@10` : DCG gradué avec pertinence `0`, `1`, `2`, divisé par l'IDCG du même oracle, puis moyenne sur les cas répondables ;
- `zero-result rate` : nombre de cas dont la recherche retourne zéro résultat, divisé par le nombre total de cas. Le rapport doit aussi séparer les cas répondables des cas `no_answer`, car un taux global seul est ambigu.

Les valeurs doivent être publiées globalement et par catégorie et langue, avec numérateur, dénominateur et nombre de cas exclus. Aucun arrondi ne doit intervenir avant le rendu final.

### Pilote

- `PASS` : exactement 20 résultats aux rangs 1 à 5 sont jugés par un humain, les quatre requêtes couvrent au moins trois sujets réels, et au moins 16 jugements valent `1` ou `2`.
- `FAIL` : le packet est complet et revu selon ces règles, mais moins de 16 jugements valent `1` ou `2`.
- `UNKNOWN` : toute entrée, identité, date, revue, hash ou condition de volume manque ou ne correspond pas.

### Gate complet de l'issue #12

- `PASS` d'exécution : 60 à 100 cas uniques sont revus par un humain, toutes les catégories sont présentes, Recall@5, MRR@10, nDCG@10 et zero-result rate sont calculés et découpés comme demandé, le benchmark complet consigne durée de build, RSS, taille d'index et latence de requête, et chaque gate de release est consigné.
- `FAIL` d'exécution : le packet revu est complet mais une règle structurelle ou un seuil préenregistré n'est pas respecté.
- `UNKNOWN` : données incomplètes, revue non humaine ou non identifiable, hash discordant, métrique absente, slice absent, seuil non défini ou preuve non rejouable.

Le seul seuil de qualité déjà adopté par le plan est `Recall@5 >= 0,80`. Aucun seuil de release n'est actuellement adopté pour MRR@10, nDCG@10 ou zero-result rate. Ces métriques doivent être mesurées, mais leur gate qualité reste `UNKNOWN` jusqu'à préenregistrement de seuils avant l'exécution concernée. Un résultat observé ne peut pas servir à choisir rétroactivement son propre seuil.

La release globale ne peut être `PASS` que si les gates de qualité requis et les gates techniques du plan sont tous `PASS` sur la même identité de corpus et de code. Le benchmark complet doit également enregistrer durée de build, RSS, taille d'index et latence de requête. Ce protocole ne les mesure pas à lui seul.

## Procédure de revue humaine

Pour chaque cas, suivre ces instructions opérationnelles :

1. **"Read the query and its filters without looking at the ranking score."**
2. **"Open the timestamped source and inspect enough surrounding transcript context to judge the passage."**
3. **"Assign relevance 2 only when the passage directly answers the information need."**
4. **"Assign relevance 1 when the passage is useful but incomplete, indirect, or needs substantial surrounding context."**
5. **"Assign relevance 0 when the passage does not help answer the information need."**
6. **"Do not infer relevance from the title, score, model output, or another reviewer."**
7. **"Record your name, the review date with timezone, and a factual note for ambiguous judgments."**

Les doublons d'un même document dans un classement ne comptent qu'une fois pour Recall. Ils restent visibles dans l'évidence et constituent un défaut à signaler. Une indisponibilité de vidéo, un timestamp invérifiable ou un oracle contesté produit `UNKNOWN` pour le cas jusqu'à résolution, pas un jugement inventé.

## Rerun et comparaison

1. Archiver le packet préparé, le packet revu et leurs SHA-256 sans les modifier.
2. Recalculer et vérifier le SHA Git, le SHA-256 de l'index et le SHA-256 du fichier de requêtes.
3. Rejouer la commande sans `--force` vers un nouveau chemin de sortie.
4. Pour une vérification de déterminisme, utiliser exactement le même commit, le même index, le même fichier de requêtes et le même `top-k`, puis comparer les fichiers octet par octet et leurs SHA-256.
5. Pour mesurer un changement, modifier une seule variable identifiée, produire un nouveau packet et conserver les deux identités. Ne jamais recopier les jugements sans vérifier que chaque `document_id`, `passage_id` et timestamp est identique.
6. Refaire la revue des résultats nouveaux, supprimés ou déplacés. Recalculer les métriques à partir du packet revu.
7. Reporter toute différence d'identité ou toute revue partielle en `UNKNOWN` jusqu'à complétion.

Commandes structurelles de contrôle :

```bash
uv run python scripts/prepare_search_relevance_evaluation.py --help
uv run python -m json.tool plans/evidence/2026-08-30-p2-query-template.json
git rev-parse --verify FULL_40_CHARACTER_GIT_COMMIT^{commit}
shasum -a 256 /ABSOLUTE/PATH/TO/search-v1.sqlite3
shasum -a 256 /ABSOLUTE/PATH/TO/p2-evaluation-packet.json
cmp /ABSOLUTE/PATH/TO/first-packet.json /ABSOLUTE/PATH/TO/replayed-packet.json
```

## Registre actuel

| Champ | Valeur |
|---|---|
| Auteur de la préparation | `UNKNOWN` |
| Date de préparation | `UNKNOWN` |
| Commit Git évalué | `UNKNOWN` |
| SHA-256 de l'index | `UNKNOWN` |
| SHA-256 du fichier de requêtes | `UNKNOWN` |
| SHA-256 du packet préparé | `UNKNOWN` |
| Reviewer humain | `UNKNOWN` |
| Date de revue | `UNKNOWN` |
| Nombre de jugements pilote | `UNKNOWN` |
| Nombre de cas release | `UNKNOWN` |
| Recall@5 | `UNKNOWN` |
| MRR@10 | `UNKNOWN` |
| nDCG@10 | `UNKNOWN` |
| Zero-result rate | `UNKNOWN` |
| Statut pilote | `UNKNOWN` |
| Statut gate complet | `UNKNOWN` |
| Statut release | `UNKNOWN` |

Le statut reste `UNKNOWN` jusqu'à ce que des personnes réelles exécutent ce protocole sur des sujets réels et conservent une preuve complète, liée aux hashes exacts.
