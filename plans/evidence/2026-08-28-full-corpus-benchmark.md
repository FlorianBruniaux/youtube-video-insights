# Preuve technique du corpus complet

**Date :** 2026-08-28, Europe/Paris  
**Statut technique :** `PASS` pour le build complet et les mesures de latence  
**Pertinence éditoriale :** `UNKNOWN`  
**État Git mesuré :** `tree_state=UNCOMMITTED`  
**HEAD de départ :** `aebd6a474244bdf000544a076675ba43481f90f5`  
**Commit d'implémentation contenant le code mesuré :** `4124f42`

Cette preuve lie les mesures aux hash des fichiers utilisés. Le HEAD ci-dessus
ne décrit pas à lui seul le code mesuré, car le lot n’était pas encore commité.
Le code mesuré est maintenant conservé dans le commit d'implémentation indiqué
ci-dessus. Le manifeste SHA-256 ci-dessous permet de le vérifier fichier par
fichier.

## Environnement

| Élément | Valeur observée |
|---|---|
| Machine | MacBook Pro `Mac17,6` |
| Processeur | Apple M5 Max, 18 coeurs |
| Mémoire | 128 Go |
| Système | macOS 26.5.1, build 25F80, arm64 |
| Python | 3.13.0 |
| uv | 0.11.20 |

Les numéros de série et identifiants matériels ne sont pas consignés.

## Manifeste du code mesuré

Le checksum du diff Git des chemins suivis parmi les dix fichiers de production,
calculé avec la commande suivante, vaut
`c7e11b0b0b7770387d1aa43097a58064f2680fc61ac1234c88406841861dd117`.

```bash
git diff -- \
  src/yt_insights/cli.py \
  src/yt_insights/cli_search.py \
  src/yt_insights/vtt_parser.py \
  src/yt_insights/search/corpus.py \
  src/yt_insights/search/preflight.py \
  src/yt_insights/search/chunker.py \
  src/yt_insights/search/models.py \
  src/yt_insights/search/query.py \
  src/yt_insights/search/service.py \
  src/yt_insights/search/sqlite_fts.py | shasum -a 256
```

`src/yt_insights/search/preflight.py` et `scripts/benchmark_search.py` étaient
non suivis, donc absents du flux `git diff`. Leurs hash individuels restent
présents dans le manifeste. L’état ciblé était :

```text
 M src/yt_insights/cli.py
 M src/yt_insights/cli_search.py
 M src/yt_insights/search/corpus.py
 M src/yt_insights/search/models.py
 M src/yt_insights/search/query.py
 M src/yt_insights/search/service.py
 M src/yt_insights/search/sqlite_fts.py
?? scripts/benchmark_search.py
?? src/yt_insights/search/preflight.py
```

| Fichier | SHA-256 |
|---|---|
| `src/yt_insights/cli.py` | `f08e1089bf47da4df29ac5b3c03a05522a87428719e26660fbb348bad5a3e11b` |
| `src/yt_insights/cli_search.py` | `513f53e9cda9d9f82f088e8a1ee7de4bf1114c3002a8e413057ec888b1c25a02` |
| `src/yt_insights/vtt_parser.py` | `57658c4db248953829878f2a9db5021846562005d9921bf8747eaba41248ea38` |
| `src/yt_insights/search/corpus.py` | `a4a8beb8f0a51e375918109996e2cc67cbfac3ca615f0f46685cc16795949e1c` |
| `src/yt_insights/search/preflight.py` | `5ddb9a8c4558c67ef17ae3147dac31d71a43f5ca009811cd3075db1d3aad802d` |
| `src/yt_insights/search/chunker.py` | `d55d1e2c10d14a4da55536bb5fc1e2b430d960969a45f62b56feda417f40bb6f` |
| `src/yt_insights/search/models.py` | `476a2455b8f0501b5009915d5e06fe45b927c14aed69cdba2e4400d09a321f1a` |
| `src/yt_insights/search/query.py` | `b991d7902c7fc7b131503a985c27f3ce98cc607d0e9eaf9d42a43978fa3b507b` |
| `src/yt_insights/search/service.py` | `4a45e437f92d2970d1aee589c61fdfb7c2048dba59330290293522a59b8118c9` |
| `src/yt_insights/search/sqlite_fts.py` | `d01a0ef240859b5a8572f4ac574f59320e46eb98f623f80ceb834a0315dd87b8` |
| `scripts/benchmark_search.py` | `523b7cee5b0ff4e44e9e598bc1ea2e3b6d7949c9a8d581cebaf9280893a793ca` |

## Build complet rejouable

Commande exacte, exécutée depuis la racine du dépôt :

```bash
TIMEFMT='real=%E user=%U sys=%S'; time .venv/bin/yt-insights index \
  --corpus-root output \
  --database /private/tmp/yt-insights-final-proof-receipt-20260828.mMbwUj/search-v1.sqlite3 \
  --all
```

Sortie brute, code de sortie `0` :

```text
Preflight candidates discovered: 3270
Preflight regular files sized: 3270
Preflight candidates excluded: 0
Preflight source bytes: 1054367610
Preflight required bytes: 2108735220
Preflight available bytes: 1157070036992
Sources discovered: 3270
Sources selected: 3270
Sources invalid: 0
Documents: 3270
Passages: 183789
real=48.75s user=41.28s sys=5.51s
```

La base publiée pèse `553168896` octets, soit environ 528 Mio. Son SHA-256 est
`9d8a6f2df62a98f0a7adeea3dafbe14ff17139444d0ba1713420a73bff358488`.
Le reçu de validation de 137 octets porte le SHA-256
`5d3a3e3db164cef63e082978f7e545700e22546c43dd0e55804b0bb98c2919de`.
Il lie l'identifiant de génération `a994ce08a2b9a1186587bbbe316a4681` au
SHA-256 de la base ci-dessus.
Ces fichiers résident sous `/private/tmp` et ne constituent pas des artefacts
versionnés.

La commande suivante a relu l’index publié :

```bash
.venv/bin/yt-insights index \
  --database /private/tmp/yt-insights-final-proof-receipt-20260828.mMbwUj/search-v1.sqlite3 \
  --status
```

Sortie brute, code de sortie `0` :

```text
Sources discovered: 3270
Sources selected: 3270
Sources invalid: 0
Documents: 3270
Passages: 183789
```

Le build complet tient le gate technique de cinq minutes. La RSS du build
courant reste `UNKNOWN` : `/usr/bin/time -l` ne pouvait pas lire
`kern.clockrate` dans le sandbox. Aucun chiffre mémoire antérieur n’est repris
comme preuve du build courant.

## Latence chaude rejouable

`scripts/benchmark_search.py` utilise `time.monotonic_ns`. Il valide d’abord
l’index, chauffe chaque requête, puis mesure les répétitions. Le p95 suit la
méthode du rang le plus proche. Le JSON ne contient pas le texte des requêtes.

Commande exacte :

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_search.py \
  --database /private/tmp/yt-insights-final-proof-receipt-20260828.mMbwUj/search-v1.sqlite3 \
  --warmup 1 \
  --repeats 20 \
  --limit 10
```

Sortie JSON brute, code de sortie `0` :

```json
{
  "database": "/private/tmp/yt-insights-final-proof-receipt-20260828.mMbwUj/search-v1.sqlite3",
  "hit_counts": [
    10,
    10,
    10,
    10,
    10,
    10,
    10,
    10,
    10,
    10
  ],
  "latency_ms": {
    "count": 200,
    "max": 14.209,
    "median": 3.954125,
    "min": 0.84475,
    "p95": 13.805541
  },
  "limit": 10,
  "query_count": 10,
  "repeats_per_query": 20,
  "schema_version": 1,
  "warmup_per_query": 1
}
```

Le p95 chaud de `13,805541 ms` tient le gate technique de 100 ms sur les dix
requêtes fixes du script. Cette mesure ne prouve pas la pertinence des résultats.

## Latence d'une nouvelle instance

Commande exacte, exécutée après le benchmark chaud dans un nouveau processus :

```bash
TIMEFMT='real=%E user=%U sys=%S'; time .venv/bin/yt-insights search "retrieval" \
  --database /private/tmp/yt-insights-final-proof-receipt-20260828.mMbwUj/search-v1.sqlite3 \
  --limit 10 \
  --json >/dev/null
```

Sortie brute, code de sortie `0` :

```text
real=0.32s user=0.26s sys=0.06s
```

Cette mesure est froide au niveau de l'application : nouvelle invocation CLI et
nouvelle instance de `SQLiteFtsIndex`. Elle inclut le démarrage Python, la lecture
du reçu et le calcul initial du SHA-256 de la base. Le cache du système de fichiers
n'a pas été purgé. Ce n'est donc pas une mesure de disque froid.

## Vérification du script

Commande :

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_benchmark_search.py
```

Résultat : `9 passed in 0.51s`. Les tests couvrent le schéma JSON, la méthode
du p95, les bornes `warmup`, `repeats` et `limit`, l’absence du texte des
requêtes dans la sortie et l’erreur propre sur une base absente.

La suite complète a été rejouée par le coordinateur avec les extras MCP et
développement :

```bash
uv --cache-dir /private/tmp/yt-insights-uv-cache run --extra mcp --extra dev pytest -q
```

Résultat : `258 passed in 2.26s`.

Le coordinateur a aussi exécuté `uv lock --check`, qui a validé 74 paquets, et
`git diff --check`, qui a terminé avec le code de sortie `0`.

## Modèle de menace du reçu

Le reçu vise les corruptions accidentelles, les remplacements locaux et les
races observables pendant l'accès. Il lie un `generation_id` au SHA-256 de la
base. Une instance valide le hash intégral lors de son premier accès, puis met
en cache cette validation tant que l'identité du fichier, y compris son `ctime`,
ne change pas.

Ce mécanisme ne fournit pas une frontière d'authenticité contre un processus
malveillant exécuté avec le même UID et capable de réécrire de façon coordonnée
la base et son reçu. Il ne faut pas le présenter comme une signature ni comme
une protection contre un attaquant local disposant de ces droits.

## Frontière de preuve

- Le corpus complet se construit et se relit sur cette machine avec le code du manifeste.
- Le benchmark mesure une recherche FTS chaude dans un seul processus.
- La mesure séparée de 0,32 s couvre une nouvelle instance avec cache disque non purgé.
- L’index de test est dérivé des VTT et n’a remplacé aucun fichier source.
- La pertinence éditoriale reste `UNKNOWN` jusqu’à la validation humaine P2.
- Le commit `4124f42` contient le code mesuré ; le commit documentaire suivant
  lie durablement cette preuve à ce SHA.
