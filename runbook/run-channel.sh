#!/bin/bash
# run-channel.sh — ajoute et analyse une chaîne YouTube de bout en bout.
#
# Usage :
#   ./runbook/run-channel.sh <slug> <url_chaine_/videos> [regex_annees]
#
# Exemples :
#   ./runbook/run-channel.sh alafrench https://www.youtube.com/@alafrench/videos '2025|2026'
#   ./runbook/run-channel.sh bytebytego https://www.youtube.com/@ByteByteGo/videos
#
# Variables d'environnement (surchargeables) :
#   YT_MODEL                       modèle LLM        (défaut: devstral-64k:latest)
#   YT_BASE_URL                    endpoint LLM      (défaut: http://127.0.0.1:11434/v1)
#   YT_INSIGHTS_MAX_TRANSCRIPT_CHARS  troncature     (défaut: 60000, couvre un podcast d'1h)
#   KEEP_LANG                      langue à garder   (ex: fr ou en. vide = pas de dédup)
#
# Pièges appris (voir runbook/README.md) :
#   - Pas de --cookies-from-browser safari : macOS bloque la lecture des binarycookies.
#   - max_transcript_chars par défaut = 10000, trop court pour les podcasts. On monte à 60000.
#   - Le filtre année fait une passe de métadonnées yt-dlp (lente sur une grosse chaîne).
#   - La dédup langue garde la langue d'origine (fr pour une chaîne FR, en pour une chaîne EN).

set -eu
cd "$(dirname "$0")/.."   # racine du repo

SLUG="${1:?slug requis (ex: alafrench)}"
URL="${2:?URL /videos de la chaîne requise}"
YEAR_RE="${3:-}"

MODEL="${YT_MODEL:-devstral-64k:latest}"
BASE_URL="${YT_BASE_URL:-http://127.0.0.1:11434/v1}"
export YT_INSIGHTS_MAX_TRANSCRIPT_CHARS="${YT_INSIGHTS_MAX_TRANSCRIPT_CHARS:-60000}"

OUT="output/$SLUG"
LOG="$OUT/run.log"
mkdir -p "$OUT" batches

echo "=============================================="
echo "  Chaîne : $SLUG"
echo "  URL    : $URL"
echo "  Modèle : $MODEL  ($BASE_URL)"
echo "  Années : ${YEAR_RE:-toutes}"
echo "=============================================="

# --- 1. Source : chaîne entière, ou batch d'URLs filtré par année -------------
if [ -n "$YEAR_RE" ]; then
  BATCH="batches/${SLUG}-filtered.txt"
  echo "[1/4] Résolution des dates (filtre: $YEAR_RE), passe métadonnées yt-dlp…"
  .venv/bin/yt-dlp --no-warnings --skip-download --ignore-errors --sleep-requests 1 \
    --print "%(upload_date)s|%(id)s" "$URL" 2>/dev/null \
    | awk -F'|' -v re="^($YEAR_RE)" '$1 ~ re {print "https://www.youtube.com/watch?v=" $2}' \
    | sort -u > "$BATCH" || true
  n=$(wc -l < "$BATCH" | tr -d ' ')
  echo "      $n vidéos retenues -> $BATCH"
  [ "$n" -eq 0 ] && { echo "Aucune vidéo dans la fenêtre. Stop."; exit 1; }
  SOURCE="$BATCH"
else
  echo "[1/4] Pas de filtre année : chaîne entière."
  SOURCE="$URL"
fi

# --- 2. Download transcripts + extraction insights ----------------------------
echo "[2/4] yt-insights run (transcripts + insights)…"
: > "$LOG"
.venv/bin/yt-insights run "$SOURCE" \
  --output-dir "$OUT" \
  --base-url "$BASE_URL" \
  --model "$MODEL" \
  --concurrency 1 \
  --sleep-requests 2 \
  2>&1 | tee -a "$LOG"

# --- 3. Dédup langue (opt-in) : garder KEEP_LANG, virer les traductions --------
if [ -n "${KEEP_LANG:-}" ]; then
  echo "[3/4] Dédup langue : on garde .$KEEP_LANG, on retire les autres pistes…"
  for L in fr en fr-orig en-orig es de; do
    [ "$L" = "$KEEP_LANG" ] && continue
    rm -f "$OUT"/insights/*."$L".json "$OUT"/insights/*."$L".md "$OUT"/transcripts/*."$L".vtt 2>/dev/null || true
  done
else
  echo "[3/4] Dédup langue ignorée (KEEP_LANG non défini)."
fi

# --- 4. Rafraîchir le catalogue global ----------------------------------------
echo "[4/4] Régénération de l'index global (scripts/build_index.py)…"
python3 scripts/build_index.py

echo ""
echo "Terminé. -> $OUT/INDEX.md"
echo "Catalogue global -> output/INDEX.md et output/CATALOG.yaml"
