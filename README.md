# Classification d'enseignes avec llm.lab et recherche web

Classe un nom d'enseigne de magasin dans une nomenclature de magasins
(`data/nmcl_magasins.csv`) grâce à un LLM du provider **llm.lab** (SSP Cloud), auquel on ajoute
un outil de recherche web : le modèle décide lui-même de chercher sur le web (function
calling) quand l'enseigne lui est inconnue, et répond à partir des résultats. llm.lab ne
propose pas cet outil, donc la recherche est assurée au choix par une instance locale de
[SearXNG](https://github.com/searxng/searxng) ou par l'API [Serper](https://serper.dev)
(voir « Fournisseurs de recherche »).

## Fonctionnement

```
prompt -> modèle llm.lab --(besoin du web ?)--> web_search -> SearXNG ou Serper -> résultats
                 ^                                                                |
                 +------------------------ réponse finale <----------------------+
```

## Fichiers

| Fichier             | Rôle                                                                 |
|---------------------|----------------------------------------------------------------------|
| `run.py`            | **Point d'entrée** : code un fichier CSV d'enseignes (regex puis LLM + web) |
| `rules/rules.py`    | Règles regex de codage (`coder()`), code `00` = hors champ           |
| `rules/evaluate_rules.py` | Contrôle des règles sur la liste annotée et la liste à coder (`uv run -m rules.evaluate_rules ...`) |
| `llm/client.py`     | Crée le client OpenAI configuré pour llm.lab (`get_client()`)        |
| `llm/classification.py` | Classification LLM d'une enseigne (prompt, validation du code)   |
| `llm/classify.py`   | Classification d'une seule enseigne par le LLM (`uv run -m llm.classify ...`) |
| `llm/prompts/classification.txt` | Le prompt envoyé au modèle (modifiable sans toucher au code) |
| `llm/websearch.py`  | Outil `web_search` + boucle de tool calls (`run_agent()`)            |
| `common/search_providers.py` | Les fournisseurs de recherche : SearXNG et Serper           |
| `common/setup_searxng.sh` | Installe SearXNG depuis les sources (sans Docker) et le lance  |
| `common/paths.py`   | Chemins communs (`ROOT`, `DATA_DIR`)                                 |
| `data/`             | Données (voir « Données sources (S3) ») : `shops.csv` (`libelle;city`, exemple), fichiers copiés du S3, sorties du codage (`*_coded.csv`, `sorties/`) |
| `docs/processus.excalidraw` | Schéma du processus complet (inputs, regex, LLM + web, sauvegarde) ; à ouvrir avec excalidraw.com ou l'extension VS Code Excalidraw |
| `.env.example`      | Modèle du fichier `.env` à créer                                     |
| `pyproject.toml`    | Dépendances Python (installées par `uv sync`)                        |
| `uv.lock`           | Versions exactes des dépendances (généré par uv)                     |

## Données sources (S3)

Les fichiers de données ne sont pas versionnés : ils sont sur le S3 du SSP Cloud, dans
`s3://projet-budget-famille/data/shop-types-classification/`, et se copient dans `data/` :

```bash
aws s3 cp s3://projet-budget-famille/data/shop-types-classification/ data/ --recursive --endpoint-url $AWS_ENDPOINT_URL
```

| Fichier | Origine | Contenu | Format |
|---|---|---|---|
| `liste_magasins_a_coder.csv` | — | Couples nom d'enseigne × commune qu'on veut coder. **Non annoté.** 13 243 lignes, 10 773 libellés distincts, commune absente pour 1 817 lignes. | TSV, latin-1, CRLF. **En-tête décalé d'une colonne** : la 1re colonne (sans nom) est un identifiant, `MAG_DEP` est en fait la 2e colonne (libellé) et `COM_DEP` la 3e (commune). Lecture : `--delimiter '\t' --encoding latin-1 --col-label 1 --col-city 2`. |
| `liste_magasins_a_coder.parquet` | dérivé du CSV ci-dessus | Mêmes données, colonnes remises en place : `ID` (int), `MAG_DEP` (libellé), `COM_DEP` (commune, `null` si absente). | Parquet (UTF-8) |
| `Liste_Pointsdevente_BDF2026.csv` | DRTI | Jeu **annoté**, utilisé pour mettre en place les règles regex (`rules/`) et pour les contrôler (`rules.evaluate_rules`). 1 111 enseignes. | CSV `;`, UTF-8, valeurs entre guillemets. Colonnes `identifiant;libelle` ; l'identifiant vaut `<code>_<n°>` (ex. `111_01;Auchan`), le code est le préfixe. |
| `nmcl_magasins.csv` | Banque de France (BdF) | Nomenclature des types de magasins, transmise avec assez peu de documentation : seuls un code et un libellé par poste. 76 postes sur 3 niveaux emboîtés (`1` → `11` → `111`). | CSV `;`, Windows-1252 (cp1252), colonnes `code;libellé`. Le code attend de l'UTF-8 : après copie, convertir avec `iconv -f cp1252 -t utf-8`. |

Points d'attention :
- Les libellés de `liste_magasins_a_coder` sont en majuscules, presque sans accents. Quelques-uns
  portent des restes de mauvais encodage (`THEA¢TRE` pour THÉÂTRE, `CRAªPERIE` pour CRÊPERIE).
- Les codes `420` et `990` de la liste annotée n'existent pas dans la nomenclature : aucune règle
  ne les produit.
- Le code `00` (« hors champ » : le libellé n'est pas une enseigne) est propre à ce projet ; il
  n'est pas dans la nomenclature BdF.

## Installation

Prérequis : Python 3.10+, [`uv`](https://docs.astral.sh/uv/), `git`, un accès internet.

```bash
uv sync                   # crée .venv et installe les dépendances (pyproject.toml)

cp .env.example .env      # puis renseigne ta clé API llm.lab dans .env
./common/setup_searxng.sh        # seulement si tu utilises SearXNG (première fois : quelques minutes)
```

Variables du `.env` :

| Variable           | Description                                                    |
|--------------------|----------------------------------------------------------------|
| `LLM_LAB_API_KEY`  | Ta clé API llm.lab                                             |
| `LLM_LAB_BASE_URL` | URL de l'API llm.lab (compatible OpenAI)                       |
| `LLM_LAB_MODEL`    | Modèle à utiliser (doit gérer le function calling)             |
| `SEARCH_PROVIDER`  | Fournisseur de recherche : `searxng` (défaut) ou `serper`      |
| `SEARXNG_URL`      | Adresse de SearXNG, par défaut `http://127.0.0.1:8888`         |
| `SERPER_API_KEY`   | Ta clé API Serper (nécessaire avec `serper`)                   |
| `SERPER_GL`, `SERPER_HL` | Pays et langue des résultats Serper, par défaut `fr`     |

## Fournisseurs de recherche

| | SearXNG | Serper |
|---|---|---|
| Nature | méta-moteur installé en local | API payante ([serper.dev](https://serper.dev)) |
| Moteurs | plusieurs (Brave, Google CSE, DuckDuckGo...), variables | Google uniquement |
| Coût | gratuit | 1 crédit par recherche (quota gratuit au départ) |
| Fiabilité | moteurs parfois bloqués (captcha, limites de requêtes) | stable |
| Prérequis | `./common/setup_searxng.sh` | `SERPER_API_KEY` dans le `.env` |

Le choix se fait, par ordre de priorité : option `--search-provider` de la commande,
variable `SEARCH_PROVIDER` du `.env`, sinon `searxng`. Si le fournisseur choisi n'est
pas utilisable (nom inconnu, clé Serper absente), la commande s'arrête avant tout appel
au modèle. Pour ajouter un autre fournisseur, voir l'en-tête de `common/search_providers.py`.

## Coder un fichier d'enseignes (`run.py`)

```bash
uv run run.py data/shops.csv --dry-run     # combien d'appels LLM seraient faits (aucun appel)
uv run run.py data/shops.csv --no-llm      # regex seulement
uv run run.py data/shops.csv --limit 5     # essai réel : 5 appels LLM au plus
uv run run.py data/shops.csv               # tout le fichier -> data/shops_coded.csv
```

Entrée : CSV `libelle;city` (comme `data/shops.csv`). Sortie : mêmes colonnes + `code`, `store_type`,
`method` (`regex` / `llm` / `error` / `pending`), `regex_status` (`code` / `hors_champ` / `ambigu` /
`aucune`), `regex_rules`, `regex_tier`, `regex_candidate_codes`, `llm_model`, `llm_search_provider`,
`llm_websearch` (`true` si le modèle a cherché sur le web), `llm_search_count`, `llm_queries`,
`llm_justification`, `llm_tokens`, `error` (+ `trace_json` avec `--trace`). Les libellés qui ne sont pas
une enseigne (« A DOMICILE », « ILLISIBLE »...) reçoivent le code `00` par les regex, sans appel LLM.
Les résultats LLM sont gardés dans `<sortie>.cache.jsonl` : relancer la commande reprend où elle s'est arrêtée.

## Utilisation

```bash
uv run -m llm.classify "ELECTRO DEPOT" --city 57
uv run -m llm.classify "AIR BNB"
uv run -m llm.classify "MK2" --city PARIS --search-provider serper
```

Un appel à la fois : une enseigne par exécution, la ville (`--city`) est optionnelle.
Résultat :

```json
{
  "code": "222",
  "libelle": "Grande surface spécialisée dans l’équipement de la maison",
  "justification": "...",
  "recherche_web": {
    "fournisseur": "searxng",
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
- `fournisseur` : le fournisseur de recherche utilisé (`searxng` ou `serper`) ;
- `lancee` : `true` si le modèle a cherché sur le web, `false` sinon (`recherches` est alors vide) ;
- pour chaque recherche : la requête, les moteurs qui ont répondu (`moteurs_utilises`),
  ceux qui ont échoué avec la raison (`moteurs_en_echec`, par ex. captcha ou trop de
  requêtes) et les résultats retenus avec leur URL et les moteurs qui les ont trouvés ;
- `erreur` apparaît en plus si le fournisseur n'a pas pu être interrogé ;
- `credits` apparaît avec Serper : crédits consommés par la recherche.

Avec SearXNG, plusieurs moteurs sont interrogés en parallèle : il n'y a pas un seul moteur
par recherche, et l'ensemble des moteurs qui répondent peut varier d'une requête à l'autre.
Avec Serper, `moteurs_utilises` vaut toujours `["google"]`.

Le code renvoyé par le modèle est vérifié contre la nomenclature (le libellé en est
recopié) ; sinon la commande échoue avec un message d'erreur et la réponse brute.
Quand le modèle fait une recherche web, la ligne `[tool] web_search(...)` s'affiche.

Pour changer la façon dont le modèle classe (règles de choix, usage de la recherche,
format), modifie `llm/prompts/classification.txt` : les mots `$nomenclature`, `$libelle` et
`$ville` sont remplacés par le code, ne les supprime pas.

Depuis un autre script Python, la boucle LLM + recherche est réutilisable ; elle renvoie
la réponse et le détail des recherches lancées :

```python
from websearch import run_agent
reponse, recherches = run_agent(
    [{"role": "user", "content": "Ta question"}],
    provider="serper",  # optionnel : sinon SEARCH_PROVIDER, sinon searxng
)
```

Pour un simple test du client sans recherche web : `uv run -m llm.client`.

## SearXNG

À utiliser seulement avec `SEARCH_PROVIDER=searxng`.

- Vérifier qu'il tourne : `curl http://127.0.0.1:8888/healthz` (répond `OK`)
- Logs : `~/searxng/searxng.log`
- Arrêter : `pkill -f searx.webapp`
- Relancer (par ex. après un redémarrage du service) : `./common/setup_searxng.sh`
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
