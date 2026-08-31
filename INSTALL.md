# Installation locale

yt-insights s’installe depuis son dépôt Git. Le projet n’est pas publié sur
PyPI. Le fichier `uv.lock` versionné fixe l’environnement de l’application et
des outils de développement.

## Prérequis

- macOS ou Linux
- Python 3.11 ou plus récent
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- `yt-dlp`, installé par le package Python

Les fonctions d’analyse demandent un backend LLM. L’indexation et la recherche
SQLite, l’export, le diagnostic et le MCP n’en demandent aucun. Le runtime lit
les VTT fournis par YouTube et ne transcrit pas l’audio.

## Installation avec uv

```bash
git clone https://github.com/FlorianBruniaux/youtube-video-insights
cd youtube-video-insights
uv sync --extra dev
uv run yt-insights --help
```

Pour exposer l’index à un client LLM par MCP, installez aussi l’extra `mcp` :

```bash
uv sync --extra mcp --extra dev
uv run yt-insights-mcp
```

La seconde commande démarre un serveur stdio. Elle attend qu’un client MCP
ouvre et pilote le flux. Sans l’extra, `yt-insights-mcp` s’arrête avec une
instruction d’installation courte et sans traceback.

## Fixer le corpus pour tous les répertoires

Un agent lancé depuis un autre projet ne doit pas dépendre de son répertoire
courant. Placez donc un chemin absolu dans
`~/.config/yt-insights/config.toml` :

```toml
data_root = "/Users/vous/Library/Application Support/yt-insights/corpus"
research_output_root = "/Users/vous/Documents/yt-insights-research"
```

Une variable convient aussi à une session ou un service :

```bash
export YT_INSIGHTS_DATA_ROOT="/chemin/absolu/vers/le/corpus"
export YT_INSIGHTS_RESEARCH_OUTPUT_ROOT="/chemin/absolu/vers/les/dossiers"
```

La priorité est la suivante : option CLI lorsqu’elle existe, variable
`YT_INSIGHTS_DATA_ROOT`, fichier TOML, puis `output` relatif au répertoire
courant. `index --corpus-root`, `index --database`, `search --database` et les
deux variables MCP restent des remplacements explicites pour une commande.

Vérifiez la résolution sans créer de répertoire ni appeler un LLM :

```bash
uv run yt-insights doctor --json
```

`--probe-backends` ajoute uniquement deux requêtes de santé vers cc-bridge et
Ollama sur localhost. Il n’envoie aucune complétion et n’affiche aucune clé.

`research_output_root` est optionnel, mais doit être absolu. Sans lui,
`research export` exige un `--output` absolu. Le répertoire courant n'est jamais
utilisé implicitement comme racine de publication.

## Acquérir et exporter pour un agent

Prévisualisez toujours une source multiple :

```bash
uv run yt-insights acquire \
  https://www.youtube.com/@DevWithAIYoutube \
  --slug dev-with-ai \
  --dry-run \
  --json
```

Une chaîne, une playlist ou un fichier batch s’arrête avant téléchargement si
`--yes` manque. Après vérification du volume et des exclusions :

```bash
uv run yt-insights acquire \
  https://www.youtube.com/@DevWithAIYoutube \
  --slug dev-with-ai \
  --yes
```

La commande récupère les sous-titres et métadonnées. Elle n’analyse le texte
avec un LLM que si `--analyze` est fourni. Une vidéo seule ne demande pas
`--yes`; `--dry-run` n’écrit jamais dans le corpus.

Exporter une source existante n’appelle aucun LLM :

```bash
uv run yt-insights export video VIDEO_ID --format vtt
uv run yt-insights export video VIDEO_ID --format txt
uv run yt-insights export video VIDEO_ID --format md
```

`vtt` conserve les octets source, `txt` nettoie le texte et `md` ajoute le titre,
la chaîne, l’identifiant, la langue, l’URL canonique, le SHA-256 source et les
timestamps. Les fichiers vont dans `<data_root>/exports`. Utilisez `--output`
pour une destination précise et `--force` pour remplacer un fichier existant.

## Alternative avec venv et pip

Cette méthode installe le checkout courant sans utiliser le lock :

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/yt-insights --help
```

Avec MCP :

```bash
.venv/bin/pip install -e ".[dev,mcp]"
```

## Construire un index de transcripts

Le corpus doit contenir les VTT produits par yt-insights dans ses répertoires
`transcripts/`. Le mode par défaut traite 50 sources pour donner un retour
rapide. Le mode complet traite toutes les sources après une vérification de
l’espace disque.

```bash
# Inventaire de la tranche ordonnée de 50 VTT, sans écriture
uv run yt-insights index --dry-run

# Tranche de 50 VTT répartie entre chaînes et langues
uv run yt-insights index --selection representative

# Corpus complet
uv run yt-insights index --all

# Vérification de l’index existant
uv run yt-insights index --status
```

La base dérivée se trouve par défaut dans
`output/.search/search-v1.sqlite3`. Les VTT restent la source de vérité et ne
sont pas modifiés.

Le reçu d'intégrité de l'index contient maintenant le SHA-256 de la base. Un
index construit avec une version de développement antérieure au commit
`4124f42` doit être reconstruit avec `yt-insights index --all` avant d'être
utilisé par la CLI ou le MCP.

Rechercher un passage :

```bash
uv run yt-insights search "context engineering"
uv run yt-insights search "context engineering" --channel ma-chaine --lang fr
uv run yt-insights search "context engineering" --limit 5 --json
```

## Lancer une recherche cumulative

Cette section suppose que `catalog.sqlite3` et `search-v1.sqlite3` existent.
Pour un corpus déjà présent, exécutez d'abord `catalog import-corpus` puis
`index --all`. Une erreur `local_index_unavailable` signifie que cette étape
manque ou que les chemins configurés ne ciblent pas la même racine.

Une session commence toujours par une évaluation locale sans réseau :

```bash
uv run yt-insights research start \
  "AI workflows in product and engineering teams" \
  --query "AI product engineering team workflows" \
  --freshness-profile standard \
  --json
```

La réponse attendue contient `session_id`, `revision`, la couverture, la
fraîcheur et `required_user_action=confirm_sufficiency_or_refresh`.

Conservez le `session_id` et la dernière `revision` retournée. Après intégration
du correctif Task 11 dans le SHA coordonné, `research status SESSION_ID --json`
expose `acquisition_history`, limité aux 100 dernières tentatives, et
`acquisition_history_truncated`. Chaque tentative contient `attempt_id`,
`status` et `items`; chaque item contient `video_id`, `status`, `error_code` et
`source_sha256`. Les clés d'idempotence, sélecteurs de cookies, transcripts et
diagnostics bruts ne sont pas exposés. Ne revendiquez pas ce contrat depuis un
checkout qui n'a pas intégré le correctif Task 11.

Chaque cycle demande si les preuves sont suffisantes. Si la réponse est non,
`refresh` autorise uniquement la découverte :

```bash
uv run yt-insights research decide SESSION_ID refresh \
  --revision CURRENT_REVISION \
  --idempotency-key UNIQUE_KEY \
  --json
uv run yt-insights research discover SESSION_ID \
  --revision DECISION_REVISION \
  --json
uv run yt-insights research candidates SESSION_ID --json
```

La découverte présente au maximum dix candidats. Après choix humain, approuvez
un à cinq IDs exacts, puis utilisez la nouvelle révision pour l'acquisition :

```bash
uv run yt-insights research approve SESSION_ID VIDEO_ID_A VIDEO_ID_B \
  --revision CANDIDATE_REVISION \
  --idempotency-key APPROVAL_KEY \
  --json
uv run yt-insights research acquire SESSION_ID \
  --revision APPROVAL_REVISION \
  --idempotency-key ACQUISITION_KEY \
  --json
```

Le workflow reconstruit et publie le catalogue et l'index une seule fois par
lot, réévalue les preuves, puis repose la question de suffisance. `retry`
reprend seulement le stage retryable enregistré. Après un lot partiel, et une
fois le correctif Task 11 intégré, il reprend uniquement les items
`failed_retryable` et ne réacquiert aucun résultat terminal enregistré.

Exporter le dossier déterministe n'appelle aucun LLM :

```bash
uv run yt-insights research export SESSION_ID --json
uv run yt-insights research export SESSION_ID \
  --output "/chemin/absolu/vers/un-projet/research/SESSION_ID" \
  --json
```

Le dossier contient `dossier.md` et `manifest.json`. Il reste séparé des VTT,
du catalogue et de l'index FTS5.

## Connecter un client MCP

Le serveur MCP donne un accès local et en lecture seule aux outils
`list_corpora`, `search_videos`, `search_passages` et `get_passage`, dans cet
ordre. Configurez deux chemins absolus pour éviter qu’un client lancé depuis un
autre répertoire lise les mauvaises bases.

La commande recommandée installe les quatre skills, le chercheur natif de chaque
client et les deux entrées MCP. Sans option de mode, elle produit seulement un
aperçu JSON ou texte et n’écrit rien :

```bash
uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --dry-run

uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --apply

uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --verify \
  --json
```

Le chemin du corpus doit être absolu. La commande résout
`yt-insights-mcp`, `claude` et `codex` comme exécutables absolus. Elle refuse un
fichier existant dont le contenu diffère et une entrée MCP déjà présente, au
lieu de les remplacer. Si la seconde inscription échoue, elle retire les
inscriptions et fichiers créés pendant cette exécution. Elle ne copie aucune
clé de fournisseur et n’installe aucun hook de routage implicite.

Pour un seul client, remplacez `both` par `claude` ou `codex`. Les
[prompts prêts à copier](examples/agent-prompts.md) couvrent l’acquisition, la
recherche sourcée, le workflow cumulatif et l’export.

Pour installer ou mettre à jour uniquement les quatre skills et les agents,
sans inspecter ni modifier les entrées MCP existantes :

```bash
uv run yt-insights setup assistants --client both --assets-only --dry-run
uv run yt-insights setup assistants --client both --assets-only --apply
uv run yt-insights setup assistants --client both --assets-only --verify
```

Ces commandes restent des écritures utilisateur explicites avec `--apply`.
Elles ne constituent pas une activation globale approuvée. Le quatrième skill
et les canaris Claude Code et Codex frais restent non installés ou `UNKNOWN` à
ce checkpoint.

La configuration manuelle ci-dessous reste disponible pour les clients qui ne
peuvent pas exécuter la commande de setup.

```json
{
  "mcpServers": {
    "yt-insights": {
      "command": "uv",
      "args": ["run", "yt-insights-mcp"],
      "cwd": "/chemin/absolu/vers/youtube-video-insights",
      "env": {
        "YT_INSIGHTS_SEARCH_DATABASE": "/chemin/absolu/vers/le/corpus/.search/search-v1.sqlite3",
        "YT_INSIGHTS_CATALOG_DATABASE": "/chemin/absolu/vers/le/corpus/catalog.sqlite3"
      }
    }
  }
}
```

Si une variable manque, le serveur dérive la base correspondante depuis
`data_root`. Définir les deux variables supprime cette dépendance au fichier de
configuration du compte qui lance le client.

## Choisir le backend LLM

La résolution suit cet ordre : endpoint explicite, cc-bridge, Ollama, puis API
Anthropic. La CLI affiche le backend, l’endpoint et le modèle réellement choisis
avant une analyse. Ce choix ne concerne que `run`, `report`, `suggest-shorts` et
`acquire --analyze`.

### Ollama

```bash
ollama serve
ollama pull qwen3:8b
uv run yt-insights run URL_YOUTUBE \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen3:8b
```

Cette recette force Ollama. Une fois Ollama sélectionné, le modèle demandé par
`--model` ou `YT_INSIGHTS_MODEL` doit correspondre exactement à un modèle
installé. Si le nom manque, yt-insights affiche les noms disponibles et la
commande `ollama pull` à exécuter. La sélection automatique d’un modèle local
ne se produit que si aucun modèle n’a été demandé.

`--model` seul ne force pas Ollama. Il conserve l’ordre automatique cc-bridge,
Ollama, Anthropic. Si cc-bridge répond au probe, le modèle lui est envoyé.

L’endpoint Ollama explicite est `http://127.0.0.1:11434/v1`. Pour changer de
modèle, gardez l’endpoint et remplacez le nom exact :

```bash
uv run yt-insights run URL_YOUTUBE \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen3:8b
```

### cc-bridge

yt-insights détecte cc-bridge sur `http://127.0.0.1:4141`. Le probe exige un
code 200 sur `/health`, puis envoie un appel de complétion minimal. Seule une
réponse 2xx ou 3xx sélectionne cc-bridge. Une réponse 4xx, dont 401, 403, 404 ou
429, une réponse 5xx, un refus de connexion ou un timeout déclenche le repli
vers Ollama, puis vers Anthropic si sa clé API existe. Ce probe confirme que la
route accepte l’appel minimal. Il ne mesure ni la qualité ni la disponibilité
future du modèle.

Le format du modèle dépend de la configuration de cc-bridge, par exemple
`anthropic/github_copilot/gpt-5-mini` pour cibler une route enregistrée.

```bash
uv run yt-insights run URL_YOUTUBE \
  --base-url http://127.0.0.1:4141/v1 \
  --model anthropic/github_copilot/gpt-5-mini
```

Avec `--base-url`, cette commande force le chemin compatible OpenAI de
cc-bridge et court-circuite la détection. Sans cette option, le même modèle suit
l’ordre automatique.

### API Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uv run yt-insights run URL_YOUTUBE \
  --backend anthropic \
  --model claude-haiku-4-5
```

`--backend anthropic` empêche tout changement silencieux vers cc-bridge ou
Ollama. En mode `auto`, l'API Anthropic reste le dernier choix lorsque sa clé
existe.

### Endpoint compatible OpenAI

Un endpoint explicite passe avant la détection automatique :

```bash
export YT_INSIGHTS_BASE_URL="https://fournisseur.example/v1"
export YT_INSIGHTS_API_KEY="..."
export YT_INSIGHTS_MODEL="nom-exact-du-modele"
uv run yt-insights run URL_YOUTUBE --backend openai
```

### MLX direct sur Apple Silicon

```bash
uv sync --extra mlx
uv run yt-insights run URL_YOUTUBE \
  --backend mlx \
  --model mlx-community/Qwen3-4B
```

Le modèle et le tokenizer sont chargés au premier appel. La concurrence est
forcée à 1 pour éviter plusieurs allocations du même modèle. Le gate automatisé
valide le routage et le chargement paresseux avec des doublures; exécutez un
canari court avec le modèle installé avant un traitement en volume.

### Limite de texte envoyée au LLM

Les générations Insights et Shorts envoient au maximum 10 000 caractères de
transcription par appel. Avant chaque appel réel, la CLI affiche le volume
`USED/TOTAL` et signale une éventuelle troncature sans imprimer le contenu. Un
cache hit ne déclenche ni appel ni message. L'index FTS conserve, lui, tous les
passages horodatés du VTT. Le découpage multi-appels des longues transcriptions
n'est pas encore implémenté.

### Absence de transcription audio

yt-insights consomme les sous-titres VTT déjà exposés par YouTube. Aucun chemin
de ce runtime ne télécharge une piste audio pour la transcrire. Une telle voie
ajouterait un coût média et calcul ainsi qu’une seconde source textuelle; elle
reste exclue tant qu’un corpus réel de vidéos sans sous-titres ne la justifie
pas.

## Vérifications locales

Les commandes suivantes ne téléchargent aucune vidéo et ne contactent aucun
LLM :

```bash
uv run --extra mcp --extra dev pytest -q
uv lock --check
git diff --check
uv run yt-insights index --dry-run
.venv/bin/python scripts/smoke_wheel.py --offline
```

Le guide [État d'implémentation](docs/IMPLEMENTATION-STATUS.md) ajoute les
scénarios de tranche 50 VTT, corpus complet, benchmark et MCP.

Le smoke wheel crée deux environnements temporaires hors checkout. L’installation
minimale exécute `doctor`, `acquire --dry-run`, `export`, `index` et `search`.
L’installation avec l’extra MCP vérifie l’ordre exact des quatre outils et les
appelle sur un petit corpus. Le smoke vérifie aussi les dix commandes
`research`, installe les quatre skills sous un HOME temporaire avec
`--assets-only`, et échoue si les clients factices Claude ou Codex sont
invoqués. En mode `--offline`, uv ne télécharge rien et le
test exige que les wheels nécessaires à la version Python active soient déjà
dans son cache. Retirez `--offline` pour remplir ce cache lors d’une première
exécution autorisée à accéder au registre Python.

Pour vérifier une installation avec un petit corpus réel, placez quelques VTT
dans un répertoire temporaire, puis lancez :

```bash
uv run yt-insights index \
  --corpus-root /chemin/vers/le/petit-corpus \
  --database /tmp/yt-insights-smoke.sqlite3
uv run yt-insights search "terme présent" \
  --database /tmp/yt-insights-smoke.sqlite3
```

## Dépannage

- `BackendNotFoundError` : démarrez Ollama ou cc-bridge, définissez
  `ANTHROPIC_API_KEY`, ou configurez un endpoint explicite.
- Modèle Ollama absent : utilisez un nom retourné par `ollama list`, ou exécutez
  la commande `ollama pull` indiquée par yt-insights.
- `yt-insights: command not found` : utilisez `uv run yt-insights`, ou activez
  `.venv` dans une installation pip.
- Index MCP indisponible : construisez-le avec `yt-insights index --all` et
  vérifiez `YT_INSIGHTS_SEARCH_DATABASE`.
- Catalogue MCP indisponible : importez le corpus avec
  `yt-insights catalog import-corpus CORPUS` et vérifiez
  `YT_INSIGHTS_CATALOG_DATABASE`.
