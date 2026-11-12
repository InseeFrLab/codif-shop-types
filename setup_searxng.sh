#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Ce script (en Bash, le langage des commandes du terminal) installe et lance
# SearXNG, un moteur de recherche web local que ton LLM utilise via l'outil
# "web_search" (voir websearch.py).
#
# Il fait 4 choses, dans l'ordre :
#   1. Télécharge le code de SearXNG depuis GitHub (dossier ~/searxng/src).
#   2. Crée un "venv" (environnement Python isolé) avec uv et y installe
#      SearXNG et ses dépendances, pour ne pas toucher à ton Python habituel.
#      (uv doit être installé : https://docs.astral.sh/uv/)
#   3. Écrit un fichier de configuration (~/searxng/settings.yml, hors du
#      dossier work pour ne pas le mélanger avec tes fichiers de projet) avec
#      une clé secrète aléatoire, le port 8888, un accès limité à ta machine
#      (127.0.0.1) et le format de réponse JSON, que websearch.py sait lire.
#   4. Lance SearXNG en arrière-plan, logs dans ~/searxng/searxng.log.
#
# Tout est rangé dans ~/searxng/ : src/ (le code), venv/ (l'environnement
# Python), settings.yml (la configuration) et searxng.log (les logs).
#
# Chaque étape est ignorée si elle a déjà été faite : tu peux relancer le
# script sans risque, par exemple après un redémarrage de ton service.
#
# Pour vérifier que SearXNG tourne en arrière-plan (au choix) :
#   - curl http://127.0.0.1:8888/healthz
#       -> s'il répond (par exemple "OK"), SearXNG tourne ; sinon la commande
#          échoue ou ne renvoie rien.
#   - pgrep -af searx.webapp
#       -> affiche le processus (son numéro PID et sa commande) s'il existe ;
#          n'affiche rien s'il est arrêté.
#   - tail -f ~/searxng/searxng.log
#       -> affiche les logs en direct (Ctrl+C pour quitter).
# Pour l'arrêter : pkill -f searx.webapp
#
# Pour changer un réglage (par exemple le port), modifie ~/searxng/settings.yml
# puis arrête le processus SearXNG en cours avant de relancer ce script : tant
# que SearXNG répond déjà, le script ne le relance pas.
#
# Notes de lecture : les lignes qui commencent par # sont des commentaires
# (ignorés à l'exécution) ; "if [ ! -d X ]" signifie "si le dossier X n'existe
# pas" ; "$VARIABLE" est la valeur d'une variable définie plus haut.
# ---------------------------------------------------------------------------

# Arrête le script à la première erreur au lieu de continuer à l'aveugle.
set -euo pipefail

DIR="$HOME/searxng"
VENV="$DIR/venv"
SETTINGS="$DIR/settings.yml"
LOG="$DIR/searxng.log"
PORT=8888

mkdir -p "$DIR"

if [ ! -d "$DIR/src" ]; then
  git clone --depth 1 https://github.com/searxng/searxng "$DIR/src"
fi

if [ ! -d "$VENV" ]; then
  uv venv "$VENV"
  uv pip install --python "$VENV/bin/python" -U setuptools wheel pyyaml
  (cd "$DIR/src" && uv pip install --python "$VENV/bin/python" -r requirements.txt \
    && uv pip install --python "$VENV/bin/python" --no-build-isolation -e .)
fi

if [ ! -f "$SETTINGS" ]; then
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$SETTINGS" <<EOF
use_default_settings: true
server:
  secret_key: "$SECRET"
  bind_address: "127.0.0.1"
  port: $PORT
  limiter: false
search:
  formats:
    - html
    - json
EOF
fi

if curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "SearXNG tourne déjà sur le port $PORT"
  exit 0
fi

cd "$DIR/src"
SEARXNG_SETTINGS_PATH="$SETTINGS" nohup "$VENV/bin/python" -m searx.webapp > "$LOG" 2>&1 &
echo "SearXNG lancé (PID $!), logs : $LOG"
