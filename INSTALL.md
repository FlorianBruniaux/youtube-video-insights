# Installation

---

## Prérequis

- macOS
- Python 3.11 ou plus récent (vérifier avec `python3 --version`)
- Xcode Command Line Tools (`xcode-select --install` si la commande `git` n'existe pas encore)
- [Claude Code](https://claude.com/claude-code) installé, pour utiliser les skills fournis avec le repo

---

## 1. Cloner le repo

```bash
git clone https://github.com/FlorianBruniaux/youtube-video-insights
cd youtube-video-insights
```

---

## 2. Installer le package

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Activer l'environnement pour les commandes suivantes :

```bash
source .venv/bin/activate
```

---

## 3. Choisir un backend LLM

yt-insights a besoin d'un LLM pour extraire les insights et les suggestions de Shorts. Quatre options, dans l'ordre où le programme les détecte automatiquement :

| Backend | Coût | Prérequis machine | Quand le choisir |
|---|---|---|---|
| cc-bridge | Inclus dans un abonnement Claude Code existant | cc-bridge lancé en arrière-plan sur le port 4141 | Un abonnement Claude Code Pro/Max est déjà payé, pas envie de facturation à l'usage séparée |
| Ollama | Gratuit | RAM/GPU suffisants pour faire tourner un modèle local (8B+ recommandé) | Machine avec assez de puissance, pas de connexion internet garantie |
| Clé API Anthropic | Facturé à l'usage | Aucun, tourne sur n'importe quelle machine | Machine sans GPU dédié, le cas le plus simple à mettre en place |
| Autre fournisseur compatible OpenAI (Gemini, etc.) | Variable selon le fournisseur | Aucun | Une clé d'un autre fournisseur est déjà disponible |

Un seul backend suffit. Ne configurer que celui qui correspond à la situation.

### Option A : clé API Anthropic (recommandé si pas de GPU)

Une clé API valide est nécessaire (`sk-ant-...`). L'exporter dans le shell courant :

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Pour que la clé survive à la fermeture du terminal, l'ajouter au profil shell :

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.zshrc
source ~/.zshrc
```

Aucun autre réglage n'est nécessaire : yt-insights utilise directement cette clé si ni cc-bridge ni Ollama ne répondent.

### Option B : Ollama (local, gratuit, demande de la puissance machine)

1. Installer Ollama depuis [ollama.com](https://ollama.com)
2. Télécharger un modèle de chat, par exemple :
   ```bash
   ollama pull llama3.2
   ```
3. Démarrer le serveur :
   ```bash
   ollama serve
   ```

yt-insights détecte automatiquement Ollama sur le port 11434 et choisit le premier modèle Llama ou Qwen trouvé. C'est l'option écartée pour une machine qui n'a pas la puissance nécessaire (GPU/RAM insuffisants), auquel cas utiliser l'option A ou C.

### Option C : cc-bridge (route via un abonnement Claude Code existant)

*Section à compléter : lien du repo cc-bridge en attente.*

### Option D : autre fournisseur compatible OpenAI (ex. Gemini)

yt-insights accepte n'importe quel fournisseur qui expose une API compatible OpenAI, via trois variables d'environnement :

```bash
export YT_INSIGHTS_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export YT_INSIGHTS_API_KEY="<clé API du fournisseur>"
export YT_INSIGHTS_MODEL="gemini-<version>"
```

L'exemple ci-dessus pointe vers l'API Gemini de Google, qui expose une couche de compatibilité OpenAI officielle à cette adresse ([documentation Google AI](https://ai.google.dev/gemini-api/docs/openai)). Le principe est le même pour tout autre fournisseur compatible OpenAI : `base_url` + `api_key` + `model` de ce fournisseur.

**Note sur MLX (Apple Silicon)** : le README mentionne un backend MLX local via `pip install yt-insights[mlx]` et `--base-url mlx`. En l'état actuel du code, ce chemin n'est pas câblé : `MLXBackend` existe dans `backends/mlx.py` mais n'est instancié nulle part dans la logique de résolution du backend (`backends/__init__.py`). Ne pas compter sur cette option tant que ce n'est pas corrigé côté code.

---

## 4. Vérifier que ça fonctionne

Tester sur une seule vidéo courte, dans un dossier de sortie séparé pour ne rien mélanger avec un run réel :

```bash
yt-insights run "URL_D_UNE_VIDEO_COURTE" --output-dir output/test
```

Résultat attendu : un `.vtt` dans `output/test/transcripts/`, un `.json` et un `.md` dans `output/test/insights/`. Si les trois fichiers existent, l'installation est correcte. Le dossier `output/test/` peut ensuite être supprimé.

---

## 5. Utiliser les skills Claude Code

Ouvrir un terminal à la racine du repo et lancer `claude`. Les skills disponibles :

| Commande | Rôle |
|---|---|
| `/yt-get-transcript [URL]` | Télécharge le transcript d'une vidéo |
| `/yt-get-insights [URL]` | Extrait les insights d'une vidéo (sujet, points clés, outils, citations) |
| `/yt-get-shorts [URL]` | Suggère des moments pour un Short, télécharge le clip choisi |
| `/yt-run-pipeline [URL]` | Enchaîne les trois étapes ci-dessus pour une vidéo |
| `/yt-add-channel [URL chaîne] [slug] [années]` | Traite une chaîne YouTube entière |

Pour une vidéo isolée, coller directement l'URL YouTube dans la conversation suffit : l'agent `yt-video-analyst` se déclenche automatiquement et demande ce qu'il faut en faire.

---

## Dépannage

- **`BackendNotFoundError`** : aucun des trois backends n'a répondu. Vérifier `echo $ANTHROPIC_API_KEY`, ou que `ollama serve` / cc-bridge tournent bien.
- **Erreur 429 sur yt-dlp** : YouTube limite le débit de téléchargement. Les skills retentent automatiquement avec les cookies du navigateur Chrome ; sinon, attendre quelques minutes avant de relancer.
- **`yt-insights: command not found`** : l'environnement virtuel n'est pas activé. Relancer `source .venv/bin/activate` dans le terminal courant.

---

## Prompt à coller dans une session Claude Code fraîche

Pour déléguer l'installation complète à Claude Code plutôt que de suivre les étapes à la main, ouvrir un terminal dans le dossier où le projet doit vivre puis lancer `claude`, et coller :

```
Installe le projet yt-insights sur cette machine en suivant INSTALL.md une fois le repo cloné :
1. Clone https://github.com/FlorianBruniaux/youtube-video-insights dans le dossier courant.
2. Crée un environnement virtuel Python et installe le package en mode editable.
3. Backend LLM : je n'ai pas de GPU dédié, utilise l'option A (clé API Anthropic) du fichier INSTALL.md. Demande-moi la clé, puis configure-la dans mon profil shell pour qu'elle persiste.
4. Lance un test sur une vidéo YouTube courte de ton choix avec --output-dir output/test et vérifie que les fichiers attendus sont bien créés.
5. Une fois validé, supprime output/test et confirme que l'installation est prête, avec la liste des skills disponibles dans .claude/skills/.

Arrête-toi et demande-moi avant toute étape qui a besoin d'une information de ma part (la clé API notamment), et signale clairement toute erreur au lieu de la contourner.
```
