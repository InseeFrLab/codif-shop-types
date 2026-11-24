# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Classifies a store brand name (`enseigne`) into a store nomenclature (`nmcl_magasins.csv`, `code;libellé`) using an LLM served by **llm.lab** (SSP Cloud, OpenAI-compatible API). llm.lab has no web search tool, so this repo provides one via function calling, backed by either a local SearXNG instance or the Serper API. The README (in French) is the user-facing documentation; code comments and prompts are also in French.

## Commands

Uses `uv` (Python 3.10+). There is no test suite or linter configured.

```bash
uv sync                          # install deps from pyproject.toml / uv.lock
cp .env.example .env             # then fill in LLM_LAB_API_KEY etc.
./setup_searxng.sh               # only for SEARCH_PROVIDER=searxng; installs SearXNG from source to ~/searxng and starts it on :8888

uv run run.py shops.csv --dry-run   # entry point: counts regex results and LLM calls that WOULD be made, no call
uv run run.py shops.csv --no-llm    # regex only (no LLM / web search)
uv run run.py shops.csv --limit 5   # real run, at most 5 LLM calls; writes shops_coded.csv (+ .cache.jsonl, resumable)

uv run classify.py "ELECTRO DEPOT" --city 57   # single brand, LLM only (no regex)
uv run classify.py "MK2" --city PARIS --search-provider serper
uv run client.py                 # smoke-test the llm.lab client without web search

curl http://127.0.0.1:8888/healthz   # SearXNG health (returns OK)
pkill -f searx.webapp                # stop SearXNG (logs: ~/searxng/searxng.log)
```

## Architecture

Entry point: **`run.py`** (a CSV of `libelle;city` in, the same CSV plus code/metadata columns out). For each row: `rules.coder()` (regex) → if the result is `code`/`hors_champ` the code is set by regex, otherwise (`ambigu`/`aucune`) `classification.classify_with_llm()` → `websearch.run_agent()` → llm.lab model ⇄ `web_search` tool → `search_providers` → SearXNG/Serper. LLM calls are made once per distinct (label, city), in parallel (`--workers`), and each success is appended to `<output>.cache.jsonl` so a rerun resumes (failed ones are retried). Added columns: `code`, `store_type`, `method` (`regex`|`llm`|`error`|`pending`), `regex_status` (`code`|`hors_champ`|`ambigu`|`aucune`), `regex_rules`, `regex_tier`, `regex_candidate_codes`, `llm_*` (model, search provider, `llm_websearch`, search count, queries, justification, tokens), `error`, optional `trace_json`. Out-of-scope labels (code `00`) are a regex *result* (`regex_status=hors_champ`, `method=regex`).

- `classification.py` — LLM classification brick shared by `run.py` and `classify.py`: `load_nomenclature`, `build_prompt`, `parse_answer`, and `classify_with_llm()` (never raises; errors, raw answer, searches, tokens are in the returned dict).
- `classify.py` — CLI for a single brand (LLM only, no regex). Uses `classification.py`: loads the nomenclature, renders `prompts/classification.txt` (a `string.Template` using `$nomenclature`, `$libelle`, `$ville`; `$` is used instead of `{}` because the prompt contains literal JSON braces), calls `run_agent`, then `parse_answer` extracts the JSON between the first `{` and last `}` and validates the code exists in the nomenclature. The `libelle` in the output is copied from the nomenclature, never from the model. Output is JSON with an added `recherche_web` trace field.
- `websearch.py` — library (no CLI). `run_agent(messages, provider)` runs the OpenAI tool-call loop (max `MAX_ITERATIONS=5`, then forces an answer with `tool_choice="none"`). `web_search` returns `(text_for_model, details_for_trace)`; the details are never shown to the model. Search errors are returned to the model as text rather than raised. The tool description in `TOOLS` steers model behavior (e.g. query = exact brand name + city only).
- `search_providers.py` — each provider is `search_xxx(query, max_results)` returning one fixed dict shape (`resultats`, `moteurs_utilises`, `moteurs_en_echec`, optional `credits`); registered in the `PROVIDERS` dict, with prerequisites in `check_provider`. Provider choice priority: `--search-provider` flag > `SEARCH_PROVIDER` env > `searxng`. `check_provider` fails before any model call.
- `client.py` — `get_client()` builds the OpenAI client from `LLM_LAB_*` env vars (loaded via python-dotenv from `.env`; never commit it).

`shops.csv` (`libelle;city`) is the sample input format for `run.py`. The BDF list `liste_magasins_a_coder.csv` is a latin-1 TSV whose header is shifted by one column: read it with `--delimiter '\t' --encoding latin-1 --col-label 1 --col-city 2`.

The agent running this repo must not trigger real LLM or web-search calls on its own (use `--dry-run` / `--no-llm`, or stub `classification.classify_with_llm`).

To change classification behavior, edit `prompts/classification.txt` rather than code. To add a search provider, follow the header of `search_providers.py`.

## Règles déterministes (regex) avant le LLM

`rules.py` code une enseigne sans LLM : `coder(libelle, tiers=("A","B"))` renvoie `{code, statut, regles, tier}` avec `statut` = `code` | `hors_champ` | `ambigu` | `aucune`. `hors_champ` (code `CODE_HORS_CHAMP = "00"`) = le libellé n'est pas une enseigne (`A DOMICILE`, `ILLISIBLE`, `VIREMENT`, `BOUTIQUE` seul...) : règles exactes uniquement, à écarter sans LLM. `ambigu` et `aucune` doivent passer par `classify.py` (LLM + web). `classify.py` n'appelle pas encore ce module.
- Règles déduites de `Liste_Pointsdevente_BDF2026.csv` (annotations `111_01;Auchan`, code = préfixe). Les codes 420 et 990 de ce fichier n'existent pas dans `nmcl_magasins.csv` : aucune règle ne les produit.
- Le niveau le plus bas qui matche gagne (0 composés prioritaires, 1 mots spécifiques, 2 mots génériques). Si plusieurs codes diffèrent au niveau gagnant, le libellé est `ambigu`, donc non codé. Les marques multi-formats (Carrefour, Leclerc, Intermarché, Casino) n'ont de règle que sur les formes distinguées par la liste annotée ; « marché » n'est jamais une règle seule.
- Tier A = déduit de la liste annotée ; tier B = extension par analogie (connaissances extérieures : VITO, ENGEN, DOUKA, `DR <nom>`, `BAR/CAFE <nom>`...), à relire.
- `uv run evaluate_rules.py REFERENCE.csv A_CODER.csv [--tiers A] [--sortie sorties] [--echantillon N] [--voir CODE]` : doit rester à 0 erreur sur la référence (un libellé annoté ne doit jamais recevoir un autre code), et produit quatre fichiers dans `sorties/` (ignoré par git) : `codes.csv` (codés sans ambiguïté), `hors_champ.csv` (code `00`, écartés), `ambigus.csv` (règles contradictoires, avec `codes_candidats`) et `non_codes.csv` (aucune règle) ; ambigus et non codés vont au LLM.
- Les fichiers viennent de `s3://projet-budget-famille/data/shop-types-classification/` (`aws s3 cp ... --endpoint-url $AWS_ENDPOINT_URL`) ; `liste_magasins_a_coder.csv` est un TSV en latin-1.

