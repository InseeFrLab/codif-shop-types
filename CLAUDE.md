# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Classifies a store brand name (`enseigne`) into a store nomenclature (`nmcl_magasins.csv` on S3, `code;libellé`, 76 codes on 3 nested levels `1` → `11` → `111`) using an LLM served by **llm.lab** (SSP Cloud, OpenAI-compatible API). llm.lab has no web search tool, so this repo provides one via function calling, backed by either a local SearXNG instance or the Serper API. The README (in French) is the user-facing documentation; code comments and prompts are also in French.

## Commands

Uses `uv` (Python 3.10+). There is no test suite or linter configured.

```bash
uv sync                          # install deps from pyproject.toml / uv.lock
cp .env.example .env             # then fill in LLM_LAB_API_KEY etc.
./common/setup_searxng.sh       # only for SEARCH_PROVIDER=searxng; installs SearXNG from source to ~/searxng and starts it on :8888

uv run run.py data/shops.csv --dry-run   # entry point: counts regex results and LLM calls that WOULD be made, no call
uv run run.py data/shops.csv --no-llm    # regex only (no LLM / web search)
uv run run.py data/shops.csv --limit 5   # real run, at most 5 LLM calls; writes data/shops_coded.csv (+ .cache.jsonl, resumable)
uv run run.py s3://projet-budget-famille/data/shop-types-classification/samples/<sample>.parquet --no-cache   # Parquet in (MAG_DEP/COM_DEP) -> S3 runs/run_JJMMAAAA_HHMM_SS/<name>_coded.parquet; --no-cache = no LLM cache (evaluation phase); --no-websearch = model called without the web_search tool (no Serper credits; use it for any real test call)

uv run -m llm.classify "ELECTRO DEPOT" --city 57   # single brand, LLM only (no regex)
uv run -m llm.classify "MK2" --city PARIS --search-provider serper
uv run -m llm.client                 # smoke-test the llm.lab client without web search
uv run -m rules.evaluate_rules data/Liste_Pointsdevente_BDF2026.csv data/liste_magasins_a_coder.csv   # check regex rules; must report 0 errors on the annotated reference (writes data/sorties/)

uv run -m llm.nomenclature_exemples [--max 10] [--output F]   # rebuild S3 nmcl_magasins_exemples.csv (nomenclature + <=10 annotated examples per code, injected in the prompt)
uv run -m sampling.draw_sample [--size 100] [--seed 42]   # reproducible random sample of the S3 parquet list -> S3 samples/..._<JJMMAAAA>.parquet (DuckDB)

curl http://127.0.0.1:8888/healthz   # SearXNG health (returns OK)
pkill -f searx.webapp                # stop SearXNG (logs: ~/searxng/searxng.log)
```

S3 side effects to keep in mind:
- Every `run.py` / `llm.classify` invocation, including `--dry-run` and `--no-llm`, loads the nomenclature from S3 and therefore needs the SSP Cloud `AWS_*` env vars (`common/s3.connect()` raises `KeyError` otherwise). Pass `--nomenclature <local file>` to stay offline.
- A `.parquet` input writes its output to the shared S3 bucket (`runs/...`) by default; use `-o <local path>.parquet` for test runs. `sampling.draw_sample` also writes to S3 unless `--output` is given.
- Input data is not versioned (only `data/shops.csv` is tracked); `data/sorties/`, `*_coded.csv` and `*.cache.jsonl` are gitignored.

## Architecture

Layout: `rules/` (regex coding: `rules.py`, `evaluate_rules.py`), `llm/` (everything calling the model: `client.py`, `classification.py`, `classify.py`, `websearch.py`, `prompts/`), `common/` (cross-cutting: `search_providers.py`, `setup_searxng.sh`, `paths.py` with `ROOT`/`DATA_DIR`, `s3.py` with `S3_DIR` and the DuckDB S3 `connect()`), `sampling/` (DuckDB random sample of the S3 list, ordered by `md5(seed-ID)` so it is reproducible and nested), `data/` (nomenclature, input lists, coded outputs, `sorties/`). `run.py`, `.env`, README and CLAUDE.md stay at the root. Directories are Python packages: use absolute imports (`from llm.websearch import ...`) and run submodules with `uv run -m pkg.module`, not `uv run pkg/module.py`.

Entry point: **`run.py`** (a CSV of `libelle;city` in, the same CSV plus code/metadata columns out). For each row: `rules.coder()` (regex) → if the result is `code`/`hors_champ` the code is set by regex, otherwise (`ambigu`/`aucune`) `llm.classification.classify_with_llm()` → `llm.websearch.run_agent()` → llm.lab model ⇄ `web_search` tool → `common.search_providers` → SearXNG/Serper. LLM calls are made once per distinct (label, city), in parallel (`--workers`), and each success is appended to `<output>.cache.jsonl` so a rerun resumes (failed ones are retried). Added columns: `code`, `store_type`, `method` (`regex`|`llm`|`error`|`pending`), `regex_status` (`code`|`hors_champ`|`ambigu`|`aucune`), `regex_rules`, `regex_tier`, `regex_candidate_codes`, `llm_*` (model, search provider, `llm_websearch`, search count, queries, justification, tokens), `error`, optional `trace_json`. Out-of-scope labels (code `00`) are a regex *result* (`regex_status=hors_champ`, `method=regex`).

- `llm/classification.py` — LLM classification brick shared by `run.py` and `classify.py`: `load_nomenclature`, `build_prompt`, `parse_answer`, and `classify_with_llm()` (never raises; errors, raw answer, searches, tokens are in the returned dict).
- `llm/classify.py` — CLI for a single brand (LLM only, no regex). Uses `classification.py`: loads the nomenclature, renders `llm/prompts/classification.txt` (a `string.Template` using `$nomenclature`, `$libelle`, `$ville`; `$` is used instead of `{}` because the prompt contains literal JSON braces), calls `run_agent`, then `parse_answer` extracts the JSON between the first `{` and last `}` and validates the code exists in the nomenclature. The `libelle` in the output is copied from the nomenclature, never from the model. Output is JSON with an added `recherche_web` trace field.
- `llm/websearch.py` — library (no CLI). `run_agent(messages, provider)` runs the OpenAI tool-call loop (max `MAX_ITERATIONS=5`, then forces an answer with `tool_choice="none"`). `web_search` returns `(text_for_model, details_for_trace)`; the details are never shown to the model. Search errors are returned to the model as text rather than raised. The tool description in `TOOLS` steers model behavior (e.g. query = exact brand name + city only).
- `common/search_providers.py` — each provider is `search_xxx(query, max_results)` returning one fixed dict shape (`resultats`, `moteurs_utilises`, `moteurs_en_echec`, optional `credits`); registered in the `PROVIDERS` dict, with prerequisites in `check_provider`. Provider choice priority: `--search-provider` flag > `SEARCH_PROVIDER` env > `searxng`. `check_provider` fails before any model call.
- `llm/client.py` — `get_client()` builds the OpenAI client from `LLM_LAB_*` env vars (loaded via python-dotenv from `.env`; never commit it).

`data/shops.csv` (`libelle;city`) is the sample input format for `run.py`. The BDF list `liste_magasins_a_coder.csv` is a latin-1 TSV whose header is shifted by one column: read it with `--delimiter '\t' --encoding latin-1 --col-label 1 --col-city 2`.

The agent running this repo must not trigger real LLM or web-search calls on its own (use `--dry-run` / `--no-llm`, or stub `run.classify_with_llm`). Serper is paid: if the user allows real LLM calls, use `--no-websearch` unless they explicitly allow Serper.

`run_llm_pass` logs one line per result (with call duration) and a heartbeat at least every 30 s (`wait(..., timeout=30)`), with an ETA. The OpenAI client has `timeout=LLM_LAB_TIMEOUT` (default 120 s) and `max_retries=1`, so a hung call becomes `method=error` instead of blocking.

To change classification behavior, edit `llm/prompts/classification.txt` rather than code. To add a search provider, follow the header of `common/search_providers.py`. `docs/processus.excalidraw` diagrams the full pipeline (inputs → regex → LLM + web → outputs).

## Deterministic regex rules (before the LLM)

`rules/rules.py`: `coder(libelle, tiers=("A","B"))` returns `{code, statut, regles, tier}` (+ `codes_candidats` when ambiguous). `hors_champ` (`CODE_HORS_CHAMP = "00"`, project-specific, not in the BdF nomenclature) means the label is not a store (`A DOMICILE`, `ILLISIBLE`, `VIREMENT`, bare `BOUTIQUE`...) and uses exact-match rules only.
- Rules are derived from the annotated `Liste_Pointsdevente_BDF2026.csv` (`111_01;Auchan`, code = prefix). Its codes 420 and 990 don't exist in the nomenclature, so no rule produces them.
- The lowest matching level wins (0 = priority compounds, 1 = specific words, 2 = generic words). If different codes match at the winning level, the label is `ambigu` and left to the LLM. Multi-format brands (Carrefour, Leclerc, Intermarché, Casino) only have rules for forms the annotated list distinguishes; « marché » alone is never a rule.
- Tier A = deduced from the annotated list; tier B = extension by analogy from outside knowledge (VITO, ENGEN, DOUKA, `DR <nom>`, `BAR/CAFE <nom>`...), to be reviewed.
- After any rule change, run `uv run -m rules.evaluate_rules REFERENCE.csv A_CODER.csv [--tiers A] [--sortie data/sorties] [--echantillon N] [--voir CODE]`: it must stay at 0 errors on the reference (an annotated label must never get another code). It writes `codes.csv`, `hors_champ.csv`, `ambigus.csv` (with `codes_candidats`), `non_codes.csv` and `reference_non_codes.txt` (annotated labels no rule covers).
- Raw labels are uppercase, mostly unaccented, with some mojibake (`THEA¢TRE`, `CRAªPERIE`); rules must tolerate this.


## S3 / Parquet

- DuckDB only (no pyarrow/pandas): `common/s3.py` `connect()` builds the S3 secret from the SSP Cloud `AWS_*` env vars; `read_bytes()` reads any `s3://` or local file.
- The nomenclature is read from S3 once per run. Default is the enriched `nmcl_magasins_exemples.csv` (`code;libellé;exemples`, UTF-8, built by `llm/nomenclature_exemples.py` from `nmcl_magasins.csv` + the annotated list): `load_nomenclature()` returns `{code: libellé}` (extra column ignored), `load_examples()` returns `{code: [examples]}` (`{}` for the raw file) and `build_prompt` appends `(exemples annotés : …)` to each code line. Prompt changes make the LLM cache stale: use `--no-cache` when evaluating.
- `run.py` with a `.parquet` input converts rows to text to reuse the CSV pipeline, then writes added columns to a temp JSONL joined back to the input parquet on `file_row_number`, so original column types are kept. Parquet cache lives in `data/cache/<name>_coded.cache.jsonl` (keyed by input, since each run has a new folder).
