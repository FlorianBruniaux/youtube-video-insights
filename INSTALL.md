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
SQLite n’en demandent aucun.

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

Rechercher un passage :

```bash
uv run yt-insights search "context engineering"
uv run yt-insights search "context engineering" --channel ma-chaine --lang fr
uv run yt-insights search "context engineering" --limit 5 --json
```

## Connecter un client MCP

Le serveur MCP donne un accès local et en lecture seule aux outils
`search_passages` et `get_passage`. Configurez un chemin absolu pour éviter
qu’un client lancé depuis un autre répertoire lise la mauvaise base.

```json
{
  "mcpServers": {
    "yt-insights": {
      "command": "uv",
      "args": ["run", "yt-insights-mcp"],
      "cwd": "/chemin/absolu/vers/youtube-video-insights",
      "env": {
        "YT_INSIGHTS_SEARCH_DATABASE": "/chemin/absolu/vers/youtube-video-insights/output/.search/search-v1.sqlite3"
      }
    }
  }
}
```

`YT_INSIGHTS_SEARCH_DATABASE` est optionnelle si le client utilise le dépôt
comme répertoire de travail et si l’index occupe son emplacement par défaut.

## Choisir le backend LLM

La résolution suit cet ordre : endpoint explicite, cc-bridge, Ollama, puis API
Anthropic. La CLI affiche le backend, l’endpoint et le modèle réellement choisis
avant une analyse.

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
  --model anthropic/github_copilot/gpt-5-mini
```

### API Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uv run yt-insights run URL_YOUTUBE --model claude-haiku-4-5
```

L’API Anthropic sert de repli lorsque cc-bridge et Ollama ne répondent pas et
que `ANTHROPIC_API_KEY` existe.

### Endpoint compatible OpenAI

Un endpoint explicite passe avant la détection automatique :

```bash
export YT_INSIGHTS_BASE_URL="https://fournisseur.example/v1"
export YT_INSIGHTS_API_KEY="..."
export YT_INSIGHTS_MODEL="nom-exact-du-modele"
uv run yt-insights run URL_YOUTUBE
```

### Limite MLX direct

Le code contient `MLXBackend`, mais le résolveur ne le câble pas. Installer
l’extra `mlx` ou utiliser `--base-url mlx` ne sélectionne donc pas MLX direct.
Ollama reste la voie locale prise en charge par le résolveur actuel.

### Limite de texte envoyée au LLM

Les générations Insights et Shorts envoient au maximum 10 000 caractères de
transcription par appel. Avant chaque appel réel, la CLI affiche le volume
`USED/TOTAL` et signale une éventuelle troncature sans imprimer le contenu. Un
cache hit ne déclenche ni appel ni message. L'index FTS conserve, lui, tous les
passages horodatés du VTT. Le découpage multi-appels des longues transcriptions
n'est pas encore implémenté.

## Vérifications locales

Les commandes suivantes ne téléchargent aucune vidéo et ne contactent aucun
LLM :

```bash
uv run pytest -q
uv build
uv run yt-insights index --dry-run
.venv/bin/python scripts/smoke_wheel.py --offline
```

Le smoke wheel crée deux environnements temporaires hors checkout. Il vérifie
l’installation minimale, puis l’extra MCP. En mode `--offline`, uv ne télécharge
rien et le test exige que les wheels nécessaires à la version Python active
soient déjà dans son cache. Retirez `--offline` pour remplir ce cache lors d’une
première exécution autorisée à accéder au registre Python.

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
