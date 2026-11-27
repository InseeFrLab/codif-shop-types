# Classification des lieux d'achat (Budget de famille)

Code les lieux d'achat déclarés dans l'enquête Budget de famille (un libellé et une commune) dans la
nomenclature des types de magasins de la BdF :

1. des **règles regex** codent les libellés sans ambiguïté, ou les écartent comme hors champ (`00`) ;
2. les autres libellés sont classés par un **LLM de llm.lab** (SSP Cloud), auquel on ajoute un outil de
   **recherche web** (SearXNG en local, ou l'API Serper) par function calling.

```
libellé + commune ─► regex ─► code ? ──oui──────────────────────────────► sortie
                                │ non (ambigu / aucune)                     ▲
                                └─► LLM llm.lab ⇄ web_search ─► JSON validé ─┘
```

📖 Données, fonctionnement du pipeline, hypothèses et points techniques :
**[docs/methodologie.md](docs/methodologie.md)**. Schéma : `docs/processus.excalidraw`.

## Installation

```bash
uv sync                    # dépendances (Python 3.10+)
cp .env.example .env       # puis remplir la clé llm.lab
./common/setup_searxng.sh  # seulement avec SEARCH_PROVIDER=searxng (installe et lance SearXNG sur :8888)
```

| Variable du `.env` | Rôle |
|---|---|
| `LLM_LAB_API_KEY`, `LLM_LAB_BASE_URL` | Accès à llm.lab (API compatible OpenAI) |
| `LLM_LAB_MODEL` | Modèle (doit gérer le function calling), ex. `qwen3-8-27b` |
| `LLM_LAB_TIMEOUT` | Délai maximal d'un appel au modèle, en secondes (défaut 120) |
| `SEARCH_PROVIDER` | `searxng` (défaut) ou `serper` |
| `SEARXNG_URL` | Défaut `http://127.0.0.1:8888` |
| `SERPER_API_KEY`, `SERPER_GL`, `SERPER_HL` | Clé Serper ; pays et langue des résultats (défaut `fr`) |

Les données sont lues sur le S3 (`s3://projet-budget-famille/data/shop-types-classification/`) avec
les variables `AWS_*` du SSP Cloud. Toute exécution, même `--dry-run`, lit la nomenclature sur le S3.

## Coder un fichier : `run.py`

```bash
S3=s3://projet-budget-famille/data/shop-types-classification
uv run run.py $S3/samples/<échantillon>.parquet --dry-run    # comptes et appels LLM prévus, sans rien appeler
uv run run.py $S3/samples/<échantillon>.parquet --no-cache   # run d'évaluation -> $S3/runs/run_<date>/<nom>_coded.parquet
uv run run.py data/shops.csv --limit 5                       # CSV local (libelle;city) -> data/shops_coded.csv
uv run run.py liste_magasins_a_coder.csv --delimiter '\t' --encoding latin-1 --col-label 1 --col-city 2
```

| Option | Effet |
|---|---|
| `-o, --output` | Fichier de sortie. Défaut : `<entrée>_coded.csv`, ou `$S3/runs/run_JJMMAAAA_HHMM_SS/<nom>_coded.parquet` pour un Parquet |
| `--delimiter`, `--encoding` | Séparateur (défaut `;`, `'\t'` pour une tabulation) et encodage (défaut utf-8) d'un CSV |
| `--col-label`, `--col-city` | Colonnes du libellé et de la commune, par nom ou position (défaut : 1re et 2e ; `MAG_DEP` et `COM_DEP` pour un Parquet) |
| `--nomenclature` | Nomenclature (défaut : `nmcl_magasins_exemples.csv` du S3, avec exemples annotés) |
| `--tiers A B` | Tiers de règles regex utilisés (défaut : les deux ; `A` = règles déduites de la liste annotée seulement) |
| `--no-llm` | Regex seulement ; le reste reste en `pending` |
| `--dry-run` | N'appelle rien, n'écrit rien |
| `--limit N` | Au plus N appels LLM (couples libellé/commune distincts) |
| `--workers N` | Appels LLM en parallèle (défaut 4) |
| `--search-provider` | `searxng` ou `serper` (défaut : `SEARCH_PROVIDER`, sinon `searxng`) |
| `--force-search` / `--no-force-search` | Au moins une recherche web par libellé (**défaut**), ou décision laissée au modèle |
| `--no-websearch` | Le modèle répond sans outil de recherche (aucun crédit Serper consommé) |
| `--no-cache` | Ni lecture ni écriture du cache LLM ; à utiliser après tout changement de prompt ou de modèle |
| `--trace` | Ajoute la colonne `trace_json` (détail des recherches) |

Sans `--no-cache`, une relance reprend là où le run s'est arrêté. Pour relire les résultats, commencer
par `llm_confidence = basse`, `code = 99` et `coherence_codes` non vide. Les colonnes de sortie sont
décrites dans la [méthodologie](docs/methodologie.md#3-le-pipeline).

## Autres commandes

```bash
# Une seule enseigne, LLM seulement (pas de regex) ; affiche le JSON et la trace des recherches
uv run -m llm.classify "ELECTRO DEPOT" --city 57 [--search-provider serper] [--no-force-search] [--nomenclature …]

# Contrôle des règles regex, à relancer après toute modification de rules/rules.py :
# doit afficher 0 FAUX sur la liste annotée ; écrit codes / hors_champ / ambigus / non_codes dans --sortie
uv run -m rules.evaluate_rules data/Liste_Pointsdevente_BDF2026.csv data/liste_magasins_a_coder.csv \
    [--tiers A] [--sortie data/sorties] [--echantillon N] [--voir 623 659]

# Échantillon aléatoire reproductible -> $S3/samples/… (même graine = même échantillon)
uv run -m sampling.draw_sample [--size 100] [--seed 42] [--output fichier.parquet]

# Régénère la nomenclature enrichie (exemples annotés par code) après une mise à jour de la liste annotée
uv run -m llm.nomenclature_exemples [--max 10] [--output fichier.csv] [--reference …] [--nomenclature …]

# Libellés équivalents qui ont reçu des codes différents, dans un fichier déjà codé
uv run -m common.coherence $S3/runs/<run>/<nom>_coded.parquet [--col-label MAG_DEP] [--col-code code] [--limite 50]

# Test du client llm.lab, sans recherche web
uv run -m llm.client
```

## SearXNG

```bash
curl http://127.0.0.1:8888/healthz   # répond OK s'il tourne
./common/setup_searxng.sh            # relancer (par ex. après un redémarrage du service)
pkill -f searx.webapp                # arrêter
```

Logs dans `~/searxng/searxng.log`, configuration dans `~/searxng/settings.yml`.

Le fichier `.env` contient les clés API : il est exclu par `.gitignore` et ne doit jamais être publié.
