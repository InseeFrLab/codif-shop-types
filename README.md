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
| `sampling/draw_sample.py` | Tirage aléatoire reproductible d'un échantillon de la liste à coder, enregistré sur le S3 (`uv run -m sampling.draw_sample`) |
| `common/paths.py`   | Chemins communs (`ROOT`, `DATA_DIR`)                                 |
| `data/`             | Données (voir « Données sources (S3) ») : `shops.csv` (`libelle;city`, exemple), fichiers copiés du S3, sorties du codage (`*_coded.csv`, `sorties/`) |
| `docs/processus.excalidraw` | Schéma du processus complet (inputs, regex, LLM + web, sauvegarde) ; à ouvrir avec excalidraw.com ou l'extension VS Code Excalidraw |
| `docs/gen_processus.py` | Génère `docs/processus.excalidraw` (`uv run docs/gen_processus.py docs/processus.excalidraw`). À relancer après tout changement du pipeline : on modifie les textes dans ce script, pas à la main dans Excalidraw. |
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
| `nmcl_magasins_exemples.csv` | dérivé des deux fichiers ci-dessus (`uv run -m llm.nomenclature_exemples`) | Nomenclature **enrichie** : pour chaque code, au plus 10 exemples tirés de la liste annotée (373 exemples sur 51 codes). C'est la nomenclature utilisée par défaut par le LLM : les exemples sont injectés dans le prompt pour qu'il applique les conventions des annotateurs (animalerie → 232, parking → 659…). | CSV `;`, UTF-8, colonnes `code;libellé;exemples`, exemples séparés par ` \| `. |

Points d'attention :
- Les libellés de `liste_magasins_a_coder` sont en majuscules, presque sans accents. Quelques-uns
  portent des restes de mauvais encodage (`THEA¢TRE` pour THÉÂTRE, `CRAªPERIE` pour CRÊPERIE).
- Les codes `420` et `990` de la liste annotée n'existent pas dans la nomenclature : aucune règle
  ne les produit.
- Le code `00` (« hors champ » : le libellé n'est pas une enseigne) est propre à ce projet ; il
  n'est pas dans la nomenclature BdF.

### Tirer un échantillon

```bash
uv run -m sampling.draw_sample                      # 100 lignes, graine 42
uv run -m sampling.draw_sample --size 500 --seed 7
```

Tire des lignes au hasard dans `liste_magasins_a_coder.parquet` (avec DuckDB) et les enregistre
dans `samples/liste_magasins_a_coder_n<taille>_seed<graine>_<JJMMAAAA>.parquet`, dans le même
dossier S3. Le tirage est reproductible : même graine, même échantillon. Pour une même graine,
l'échantillon de 100 est inclus dans celui de 200. Les accès S3 viennent des variables `AWS_*`
du SSP Cloud.

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

### Sur un Parquet du S3

```bash
S3=s3://projet-budget-famille/data/shop-types-classification
uv run run.py $S3/samples/liste_magasins_a_coder_n200_seed18_26112026.parquet --dry-run
uv run run.py $S3/samples/liste_magasins_a_coder_n200_seed18_26112026.parquet --no-cache
```

- Le libellé et la commune sont lus dans les colonnes `MAG_DEP` et `COM_DEP` (modifiable avec
  `--col-label` / `--col-city`).
- La sortie est un Parquet au même nom suivi de `_coded`, dans un dossier par run :
  `$S3/runs/run_JJMMAAAA_HHMM_SS/<nom>_coded.parquet` (`-o` pour écrire ailleurs). Les colonnes
  d'origine gardent leur type ; `llm_websearch` est un booléen, `llm_search_count` et `llm_tokens`
  des entiers, les cases vides valent `null`.
- Cache : comme chaque run a son propre dossier, le cache LLM est gardé en local, lié au fichier
  d'entrée (`data/cache/<nom>_coded.cache.jsonl`). **`--no-cache`** le désactive : tout est
  reclassé à chaque run, ce qui convient à la phase d'évaluation.
- La nomenclature est lue sur le S3 (`$S3/nmcl_magasins_exemples.csv`), une fois par run, puis
  injectée dans chaque prompt avec ses exemples annotés (`--nomenclature $S3/nmcl_magasins.csv`
  pour revenir au prompt sans exemples).
- Pour régénérer la nomenclature enrichie après une mise à jour de la liste annotée :
  `uv run -m llm.nomenclature_exemples [--max 10] [--output <fichier local>]`. Les exemples sont
  choisis automatiquement : variantes regroupées (Boucher / Boucherie…), groupes les plus fournis
  d'abord, libellé le plus court de chaque groupe. Changer de prompt invalide le cache LLM :
  utiliser `--no-cache` pour évaluer.

### Suivi d'un run et options utiles

- Une ligne s'affiche à chaque réponse du LLM (code, nombre de recherches, durée de l'appel), et
  une ligne de suivi au moins toutes les 30 s : `… 12/8387 terminés, 4 en cours, 0 erreur(s),
  6 min écoulées, fin estimée dans ~2 h 10`.
- Un appel au modèle qui ne répond pas est abandonné au bout de 120 s (variable
  `LLM_LAB_TIMEOUT` du `.env` pour changer ce délai) : la ligne passe en `error` au lieu de
  bloquer le run.
- `--no-websearch` : le modèle répond sans outil de recherche (aucun appel SearXNG ni Serper,
  donc aucun crédit consommé). Utile pour tester, ou pour comparer avec/sans recherche ;
  `llm_search_provider` vaut alors `none`.

Entrée : CSV `libelle;city` (comme `data/shops.csv`). Sortie : mêmes colonnes + `code`, `store_type`,
`method` (`regex` / `llm` / `error` / `pending`), `regex_status` (`code` / `hors_champ` / `ambigu` /
`aucune`), `regex_rules`, `regex_tier`, `regex_candidate_codes`, `llm_model`, `llm_search_provider`,
`llm_websearch` (`true` si le modèle a cherché sur le web), `llm_search_count`, `llm_queries`,
`llm_justification`, `llm_confidence`, `llm_tokens`, `error`, `coherence_codes` (+ `trace_json` avec `--trace`). Les libellés qui ne sont pas
une enseigne (« A DOMICILE », « ILLISIBLE »...) reçoivent le code `00` par les regex, sans appel LLM.

`llm_confidence` est la confiance déclarée par le modèle :
- `haute` : activité écrite dans le libellé, ou enseigne identifiée sans ambiguïté par une source
  trouvée en recherche (une enseigne « connue » mais non vérifiée ne peut pas être `haute`) ;
- `moyenne` : activité sûre, hésitation entre deux codes voisins ;
- `basse` : activité incertaine ; le code est alors `99` ou un code de regroupement.
`coherence_codes` signale les incohérences : en fin de run, les libellés équivalents (mêmes mots
une fois retirés la forme juridique SAS/SA/SNC…, l'article initial et le pluriel, par exemple
« LES VERGERS DE VENDEE » et « VERGER DE VENDEE ») sont regroupés ; si un groupe a reçu des codes
différents, chacune de ses lignes porte la liste de ces codes (ex. `129,323`), et le run les affiche.
La commune n'entre pas dans la clé : deux homonymes dans deux villes peuvent être signalés à tort.
Sur un fichier déjà codé : `uv run -m common.coherence <fichier_coded.parquet>`.

Le modèle doit répondre `99` quand l'enseigne n'est identifiée que de façon probable. Pour la
relecture humaine, commencer par `llm_confidence = basse` et `code = 99`. Les réponses lues dans un
cache antérieur à ce champ ont `llm_confidence` vide.
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
