# LLM llm.lab avec recherche web

Un client OpenAI branché sur le provider **llm.lab** (SSP Cloud), auquel on ajoute un
outil de recherche web : le modèle décide lui-même de chercher sur le web (function
calling) et répond à partir des résultats. llm.lab ne propose pas cet outil, donc la
recherche est assurée par une instance locale de [SearXNG](https://github.com/searxng/searxng).

## Fonctionnement

```
question -> modèle llm.lab --(besoin du web ?)--> web_search -> SearXNG -> résultats
                 ^                                                          |
                 +-------------------- réponse finale <---------------------+
```

## Fichiers

| Fichier             | Rôle                                                                 |
|---------------------|----------------------------------------------------------------------|
| `client.py`         | Crée le client OpenAI configuré pour llm.lab (`get_client()`)        |
| `websearch.py`      | Outil `web_search` + boucle de tool calls (`chat()`)                 |
| `setup_searxng.sh`  | Installe SearXNG depuis les sources (sans Docker) et le lance        |
| `.env.example`      | Modèle du fichier `.env` à créer                                     |
| `requirements.txt`  | Dépendances Python                                                   |

## Installation

Prérequis : Python 3.10+, [`uv`](https://docs.astral.sh/uv/), `git`, un accès internet.

```bash
uv venv                   # crée l'environnement .venv
uv pip install -r requirements.txt

cp .env.example .env      # puis renseigne ta clé API llm.lab dans .env
./setup_searxng.sh        # installe et lance SearXNG (première fois : quelques minutes)
```

Variables du `.env` :

| Variable           | Description                                                    |
|--------------------|----------------------------------------------------------------|
| `LLM_LAB_API_KEY`  | Ta clé API llm.lab                                             |
| `LLM_LAB_BASE_URL` | URL de l'API llm.lab (compatible OpenAI)                       |
| `LLM_LAB_MODEL`    | Modèle à utiliser (doit gérer le function calling)             |
| `SEARXNG_URL`      | Adresse de SearXNG, par défaut `http://127.0.0.1:8888`         |

## Utilisation

```bash
uv run websearch.py "Quelle est la dernière version de Python ?"
```

Quand le modèle fait une recherche, la ligne `[tool] web_search(...)` s'affiche. Depuis
un autre script :

```python
from websearch import chat
print(chat("Ta question"))
```

Pour un simple test du client sans recherche web : `uv run client.py`.

## SearXNG

- Vérifier qu'il tourne : `curl http://127.0.0.1:8888/healthz` (répond `OK`)
- Logs : `~/searxng/searxng.log`
- Arrêter : `pkill -f searx.webapp`
- Relancer (par ex. après un redémarrage du service) : `./setup_searxng.sh`
- Configuration : `~/searxng/settings.yml`

## Limites

- SearXNG s'arrête si le service redémarre ; il faut le relancer.
- Certains moteurs (par ex. DuckDuckGo) peuvent afficher un captcha selon l'IP ; la
  recherche fonctionne quand même via les autres moteurs.
- Le modèle peut s'appuyer sur ses connaissances plutôt que sur les résultats ; un
  message `system` demandant de privilégier les résultats de recherche peut aider.

## Sécurité

Le fichier `.env` contient ta clé API : il est exclu par `.gitignore` et ne doit
jamais être publié. Seul `.env.example` (sans clé) est versionné.
