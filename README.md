# Classification d'enseignes avec llm.lab et recherche web

Classe un nom d'enseigne de magasin dans une nomenclature de magasins
(`nmcl_magasins.csv`) grâce à un LLM du provider **llm.lab** (SSP Cloud), auquel on ajoute
un outil de recherche web : le modèle décide lui-même de chercher sur le web (function
calling) quand l'enseigne lui est inconnue, et répond à partir des résultats. llm.lab ne propose pas cet outil, donc la
recherche est assurée par une instance locale de [SearXNG](https://github.com/searxng/searxng).

## Fonctionnement

```
prompt -> modèle llm.lab --(besoin du web ?)--> web_search -> SearXNG -> résultats
                 ^                                                          |
                 +-------------------- réponse finale <---------------------+
```

## Fichiers

| Fichier             | Rôle                                                                 |
|---------------------|----------------------------------------------------------------------|
| `client.py`         | Crée le client OpenAI configuré pour llm.lab (`get_client()`)        |
| `classify.py`       | Commande de classification d'une enseigne (`uv run classify.py ...`) |
| `prompts/classification.txt` | Le prompt envoyé au modèle (modifiable sans toucher au code) |
| `websearch.py`      | Outil `web_search` + boucle de tool calls (`run_agent()`)            |
| `nmcl_magasins.csv` | La nomenclature de magasins (`code;libellé`)                         |
| `shops.csv`         | Enseignes à classer (`libelle;city`), pour les futurs traitements    |
| `setup_searxng.sh`  | Installe SearXNG depuis les sources (sans Docker) et le lance        |
| `.env.example`      | Modèle du fichier `.env` à créer                                     |
| `pyproject.toml`    | Dépendances Python (installées par `uv sync`)                        |
| `uv.lock`           | Versions exactes des dépendances (généré par uv)                     |

## Installation

Prérequis : Python 3.10+, [`uv`](https://docs.astral.sh/uv/), `git`, un accès internet.

```bash
uv sync                   # crée .venv et installe les dépendances (pyproject.toml)

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
uv run classify.py "ELECTRO DEPOT" --city 57
uv run classify.py "AIR BNB"
```

Un appel à la fois : une enseigne par exécution, la ville (`--city`) est optionnelle.
Résultat :

```json
{
  "code": "222",
  "libelle": "Grande surface spécialisée dans l’équipement de la maison",
  "justification": "...",
  "recherche_web": {
    "lancee": true,
    "recherches": [
      {
        "requete": "ELECTRO DEPOT activité",
        "moteurs_utilises": ["brave", "google cse"],
        "moteurs_en_echec": [{"moteur": "duckduckgo", "raison": "CAPTCHA"}],
        "resultats": [
          {"titre": "...", "url": "https://...", "moteurs": ["brave", "google cse"]}
        ]
      }
    ]
  }
}
```

Le champ `recherche_web` trace les recherches web du modèle :
- `lancee` : `true` si le modèle a cherché sur le web, `false` sinon (`recherches` est alors vide) ;
- pour chaque recherche : la requête, les moteurs qui ont répondu (`moteurs_utilises`),
  ceux qui ont échoué avec la raison (`moteurs_en_echec`, par ex. captcha ou trop de
  requêtes) et les résultats retenus avec leur URL et les moteurs qui les ont trouvés ;
- `erreur` apparaît en plus si SearXNG n'a pas pu être interrogé.

SearXNG interroge plusieurs moteurs en parallèle : il n'y a pas un seul moteur par
recherche, et l'ensemble des moteurs qui répondent peut varier d'une requête à l'autre.

Le code renvoyé par le modèle est vérifié contre la nomenclature (le libellé en est
recopié) ; sinon la commande échoue avec un message d'erreur et la réponse brute.
Quand le modèle fait une recherche web, la ligne `[tool] web_search(...)` s'affiche.

Pour changer la façon dont le modèle classe (règles de choix, usage de la recherche,
format), modifie `prompts/classification.txt` : les mots `$nomenclature`, `$libelle` et
`$ville` sont remplacés par le code, ne les supprime pas.

Depuis un autre script Python, la boucle LLM + recherche est réutilisable ; elle renvoie
la réponse et le détail des recherches lancées :

```python
from websearch import run_agent
reponse, recherches = run_agent([{"role": "user", "content": "Ta question"}])
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
