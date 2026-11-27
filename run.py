"""
run.py : point d'entrée. Code les enseignes d'un fichier CSV dans la nomenclature de magasins.

PRINCIPE
    Pour chaque ligne (libellé d'enseigne + commune) :
      1. les règles regex (rules.py) essaient de coder le libellé :
           - "code"       : le code est posé par la regex ;
           - "hors_champ" : le libellé n'est pas une enseigne -> code "00", sans LLM ;
           - "ambigu"     : des règles se contredisent  } -> envoyés au LLM
           - "aucune"     : aucune règle ne matche      }    (classification.py, avec recherche web)
      2. le LLM classe les couples (libellé, commune) restants. Chaque résultat est
         écrit dans un cache : relancer la commande reprend où elle s'est arrêtée
         (CSV : <sortie>.cache.jsonl ; Parquet : data/cache/<nom>_coded.cache.jsonl ;
         --no-cache : pas de cache). La nomenclature est lue sur le S3, une fois par run.
    Le fichier de sortie reprend toutes les colonnes d'entrée, plus les colonnes ci-dessous.

COLONNES AJOUTÉES
    code, store_type         code de la nomenclature et son libellé ("00" = hors champ)
    method                   qui a posé le code : regex | llm | error (échec du LLM) | pending (pas traité)
    regex_status             résultat de la passe regex : code | hors_champ | ambigu | aucune
    regex_rules, regex_tier  règles gagnantes (ex. 412-03A) et leur tier (A = déduit de la liste annotée,
                             B = extension à relire)
    regex_candidate_codes    codes en conflit pour un libellé ambigu (ex. 411,413)
    llm_model, llm_search_provider
    llm_websearch            true/false : le modèle a-t-il cherché sur le web ?
    llm_search_count, llm_queries, llm_justification, llm_tokens
    llm_confidence           haute | moyenne | basse : confiance déclarée par le modèle (basse = à relire)
    error                    message d'erreur si le LLM a échoué
    coherence_codes          codes divergents du groupe de libellés équivalents (ex. 129,323), vide si
                             cohérent (voir common/coherence.py) : lignes à relire
    trace_json               (option --trace) détail complet des recherches web

UTILISATION
    uv run run.py data/shops.csv                       # écrit data/shops_coded.csv
    uv run run.py data/shops.csv --dry-run             # compte les appels LLM, sans en faire
    uv run run.py data/shops.csv --no-llm              # regex seulement
    uv run run.py data/shops.csv --limit 5             # au plus 5 appels LLM (pour un essai)
    uv run run.py data/shops.csv --workers 8 --search-provider serper
    Parquet sur le S3 (colonnes MAG_DEP / COM_DEP par défaut) -> Parquet dans
    <S3>/runs/run_JJMMAAAA_HHMM_SS/<nom>_coded.parquet :
    uv run run.py s3://projet-budget-famille/data/shop-types-classification/samples/<échantillon>.parquet
    uv run run.py s3://.../<échantillon>.parquet --no-cache   # phase d'évaluation : sans cache LLM
    Fichier de la BDF (TSV, latin-1) :
    uv run run.py liste_magasins_a_coder.csv --delimiter '\\t' --encoding latin-1 \\
        --col-label 1 --col-city 2      # (l'en-tête de ce fichier est décalé : colonne 0 = numéro de ligne)

PRÉREQUIS pour les appels LLM : .env rempli (LLM_LAB_*), et fournisseur de recherche disponible
    (SearXNG lancé via ./common/setup_searxng.sh, ou SERPER_API_KEY renseignée).
"""

import argparse
import collections
import csv
import json
import os
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import rules
from common.coherence import colonne_coherence, groupes_divergents, resume
from common.paths import DATA_DIR
from common.s3 import S3_DIR, connect
from common.search_providers import PROVIDERS, check_provider, resolve_provider
from llm.classification import DEFAULT_NOMENCLATURE, classify_with_llm, load_examples, load_nomenclature

ADDED_COLUMNS = [
    "code", "store_type", "method",
    "regex_status", "regex_rules", "regex_tier", "regex_candidate_codes",
    "llm_model", "llm_search_provider", "llm_websearch", "llm_search_count", "llm_queries",
    "llm_justification", "llm_confidence", "llm_tokens", "error", "coherence_codes",
]


# --------------------------------------------------------------------------------------
# Lecture du fichier d'entrée
# --------------------------------------------------------------------------------------
def read_input(path: Path, delimiter: str, encoding: str, col_label: str | None, col_city: str | None):
    """Renvoie (en-tête, lignes, index du libellé, index de la commune ou None)."""
    with open(path, encoding=encoding, newline="") as f:
        rows = [r for r in csv.reader(f, delimiter=delimiter) if r]
    if not rows:
        raise SystemExit(f"Fichier vide : {path}")
    header, body = rows[0], rows[1:]
    width = max(len(header), *(len(r) for r in body)) if body else len(header)
    header = header + [""] * (width - len(header))
    body = [r + [""] * (width - len(r)) for r in body]  # lignes plus courtes que l'en-tête
    return header, body, column_index(header, width, col_label, 0), column_index(header, width, col_city, 1)


def column_index(header: list[str], width: int, spec: str | None, default: int | None = None) -> int | None:
    """`spec` = nom de colonne, ou position (0 = première) quand l'en-tête est inutilisable."""
    if spec is None:
        return default if default is not None and default < width else None
    if spec in header:
        return header.index(spec)
    if spec.isdigit() and int(spec) < width:
        return int(spec)
    raise SystemExit(f"Colonne {spec!r} introuvable (en-tête : {header})")


def read_parquet_input(con, path: str):
    """Lit un Parquet (s3:// ou local). Renvoie (en-tête, lignes en texte), dans l'ordre du fichier."""
    con.execute("SELECT * EXCLUDE file_row_number FROM read_parquet(?, file_row_number = true) ORDER BY file_row_number", [path])
    header = [d[0] for d in con.description]
    body = [["" if v is None else str(v) for v in row] for row in con.fetchall()]
    if not body:
        raise SystemExit(f"Fichier vide : {path}")
    return header, body


def write_parquet_output(con, input_path: str, output: str, added_rows: list[dict]) -> None:
    """Écrit le Parquet de sortie : colonnes d'origine (avec leurs types) + colonnes ajoutées.

    Les colonnes ajoutées passent par un JSONL temporaire, joint au fichier d'entrée sur le
    numéro de ligne.
    """
    def typed(values: dict) -> dict:
        out = {k: (v if v != "" else None) for k, v in values.items()}
        for k in ("llm_search_count", "llm_tokens"):
            out[k] = int(out[k]) if out[k] is not None else None
        out["llm_websearch"] = {"true": True, "false": False}.get(out["llm_websearch"])
        return out

    columns = {c: "VARCHAR" for c in added_rows[0]}
    columns.update(llm_search_count="INTEGER", llm_tokens="INTEGER", llm_websearch="BOOLEAN")
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8", delete=False) as tmp:
        for i, values in enumerate(added_rows):
            tmp.write(json.dumps({"__row": i, **typed(values)}, ensure_ascii=False) + "\n")
    try:
        cols_sql = "{" + ", ".join(f"'{c}': '{t}'" for c, t in {"__row": "BIGINT", **columns}.items()) + "}"
        added = ", ".join(f"a.{c}" for c in columns)
        con.execute(f"""
            COPY (
                SELECT s.* EXCLUDE (file_row_number), {added}
                FROM read_parquet(?, file_row_number = true) s
                JOIN read_json(?, format = 'newline_delimited', columns = {cols_sql}) a
                  ON s.file_row_number = a.__row
                ORDER BY s.file_row_number
            ) TO '{output}' (FORMAT parquet)
        """, [input_path, tmp.name])
    finally:
        os.unlink(tmp.name)


# --------------------------------------------------------------------------------------
# Cache des résultats LLM (reprise après interruption)
# --------------------------------------------------------------------------------------
def load_cache(path: Path) -> dict[tuple[str, str], dict]:
    """Relit les résultats LLM déjà obtenus. Les échecs ne sont pas gardés : ils sont retentés."""
    cache: dict[tuple[str, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if not entry["result"].get("error"):
                cache[tuple(entry["key"])] = entry["result"]
    return cache


# --------------------------------------------------------------------------------------
# Passe LLM
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def duration(seconds: float) -> str:
    """Durée lisible : 42 s, 6 min, 2 h 10."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"


def run_llm_pass(keys, cache_path, nomenclature, provider, workers, websearch=True, exemples=None, force_search=True) -> dict[tuple[str, str], dict]:
    """Classe les couples (libellé, commune) avec le LLM. Renvoie {clé: résultat} (échecs inclus).

    Affichage en direct : une ligne par résultat, et une ligne de suivi au moins toutes les
    HEARTBEAT secondes (avancement, appels en cours, estimation de la fin).
    cache_path = None : rien n'est écrit dans le cache (option --no-cache).
    """
    HEARTBEAT = 30

    def timed_call(label, city):
        start = time.monotonic()
        result = classify_with_llm(label, city, nomenclature, provider, websearch=websearch, exemples=exemples,
                                   force_search=force_search)
        return result, time.monotonic() - start

    results: dict[tuple[str, str], dict] = {}
    n_errors = 0
    t0 = last_log = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(timed_call, label, city): (label, city) for label, city in keys}
    pending = set(futures)
    try:
        with open(cache_path or os.devnull, "a", encoding="utf-8") as cache_file:
            while pending:
                finished, pending = wait(pending, timeout=HEARTBEAT, return_when=FIRST_COMPLETED)
                for future in finished:
                    key = futures[future]
                    result, seconds = future.result()  # classify_with_llm ne lève pas d'exception
                    result["provider"] = provider if websearch else "none"
                    results[key] = result
                    if result["error"]:
                        n_errors += 1
                    else:
                        cache_file.write(json.dumps({"key": list(key), "result": result}, ensure_ascii=False) + "\n")
                        cache_file.flush()
                    status = (f"{result['code']} ({result.get('confidence') or '?'})" if result["code"]
                              else f"ERREUR ({result['error']})")
                    web = f"{len(result['searches'])} recherche(s)" if result["searches"] else "sans recherche"
                    log(f"[LLM {len(results)}/{len(keys)}] {key[0]!r} ({key[1]!r}) -> {status} ; {web} ; {duration(seconds)}")
                now = time.monotonic()
                if pending and now - last_log >= HEARTBEAT:
                    elapsed = now - t0
                    eta = f", fin estimée dans ~{duration(elapsed / len(results) * len(pending))}" if results else ""
                    log(f"  … {len(results)}/{len(keys)} terminés, {min(len(pending), workers)} en cours, "
                        f"{n_errors} erreur(s), {duration(elapsed)} écoulées{eta}")
                    last_log = now
    except KeyboardInterrupt:
        log("\nInterruption : les résultats déjà obtenus sont conservés (reprise possible).")
        executor.shutdown(wait=False, cancel_futures=True)
    else:
        executor.shutdown()
    log(f"  passe LLM terminée : {len(results)} résultats, {n_errors} erreur(s), {duration(time.monotonic() - t0)}")
    return results


# --------------------------------------------------------------------------------------
# Construction des lignes de sortie
# --------------------------------------------------------------------------------------
def build_output_values(regex: dict, llm: dict | None, nomenclature: dict[str, str], trace: bool) -> dict:
    """Valeurs des colonnes ajoutées pour une ligne."""
    values = dict.fromkeys(ADDED_COLUMNS + ["trace_json"], "")
    values["regex_status"] = regex["statut"]
    values["regex_rules"] = ",".join(regex["regles"])
    values["regex_tier"] = regex["tier"] or ""
    if regex["statut"] == "ambigu":
        values["regex_candidate_codes"] = ",".join(regex["codes_candidats"])

    if regex["statut"] in ("code", "hors_champ"):  # code posé par la regex
        values["method"] = "regex"
        values["code"] = regex["code"]
        values["store_type"] = rules.HORS_CHAMP_LABEL if regex["statut"] == "hors_champ" else nomenclature.get(regex["code"], "")
        return values

    if llm is None:  # pas (encore) traité par le LLM
        values["method"] = "pending"
        return values

    values["llm_model"] = llm["model"]
    values["llm_search_provider"] = llm["provider"]
    values["llm_websearch"] = "true" if llm["searches"] else "false"
    values["llm_search_count"] = len(llm["searches"])
    values["llm_queries"] = " | ".join(s.get("requete", "") for s in llm["searches"])
    values["llm_justification"] = llm["justification"]
    values["llm_confidence"] = llm.get("confidence", "")  # absent des anciennes entrées du cache
    values["llm_tokens"] = llm["tokens"]
    if trace:
        values["trace_json"] = json.dumps(llm["searches"], ensure_ascii=False)
    if llm["error"]:
        values["method"] = "error"
        values["error"] = llm["error"]
    else:
        values["method"] = "llm"
        values["code"] = llm["code"]
        values["store_type"] = nomenclature.get(llm["code"], "")
    return values


def main() -> int:
    ap = argparse.ArgumentParser(description="Code les enseignes d'un fichier CSV : regex d'abord, LLM + recherche web ensuite.")
    ap.add_argument("input", help="fichier CSV local, ou Parquet (s3:// ou local) : libellé de l'enseigne et commune")
    ap.add_argument("-o", "--output", help="fichier de sortie (défaut : <entrée>_coded.csv pour un CSV ; "
                    "pour un Parquet, <S3>/runs/run_JJMMAAAA_HHMM_SS/<nom>_coded.parquet)")
    ap.add_argument("--delimiter", default=";", help="séparateur du fichier d'entrée et de sortie (défaut ';' ; '\\t' pour une tabulation)")
    ap.add_argument("--encoding", default="utf-8", help="encodage du fichier d'entrée (défaut utf-8 ; la sortie est en utf-8)")
    ap.add_argument("--col-label", help="colonne du libellé : nom, ou position à partir de 0 (défaut : 1re colonne ; MAG_DEP pour un Parquet)")
    ap.add_argument("--col-city", help="colonne de la commune : nom, ou position à partir de 0 (défaut : 2e colonne ; COM_DEP pour un Parquet)")
    ap.add_argument("--nomenclature", default=DEFAULT_NOMENCLATURE, help="nomenclature, s3:// ou locale (défaut : nmcl_magasins_exemples.csv du S3, avec exemples annotés)")
    ap.add_argument("--tiers", nargs="+", default=["A", "B"], choices=["A", "B"], help="tiers de règles regex utilisés (défaut : A B)")
    ap.add_argument("--no-llm", action="store_true", help="regex seulement : les lignes non codées restent en 'pending'")
    ap.add_argument("--dry-run", action="store_true", help="affiche les comptes et le nombre d'appels LLM prévus, sans rien appeler ni écrire")
    ap.add_argument("--limit", type=int, help="nombre maximal d'appels LLM (couples libellé/commune distincts) ; le reste reste en 'pending'")
    ap.add_argument("--workers", type=int, default=4, help="appels LLM en parallèle (défaut 4)")
    ap.add_argument("--search-provider", choices=list(PROVIDERS), help="fournisseur de recherche web (défaut : SEARCH_PROVIDER du .env, sinon searxng)")
    ap.add_argument("--trace", action="store_true", help="ajoute la colonne trace_json (détail des recherches web)")
    ap.add_argument("--force-search", action=argparse.BooleanOptionalAction, default=True,
                    help="impose au moins une recherche web par libellé envoyé au LLM (défaut) ; "
                         "--no-force-search laisse le modèle décider s'il cherche")
    ap.add_argument("--no-websearch", action="store_true", help="le modèle répond sans l'outil de recherche web (aucun appel SearXNG/Serper)")
    ap.add_argument("--no-cache", action="store_true", help="ni lecture ni écriture du cache LLM : tout est reclassé (phase d'évaluation)")
    args = ap.parse_args()

    delimiter = "\t" if args.delimiter == "\\t" else args.delimiter
    tiers = tuple(args.tiers)
    nomenclature = load_nomenclature(args.nomenclature)  # chargée une fois, injectée dans chaque prompt
    exemples = load_examples(args.nomenclature)  # {} si la nomenclature n'a pas de colonne exemples

    parquet = args.input.endswith(".parquet")
    if parquet:
        # Sortie dans un dossier de run horodaté ; cache local lié au fichier d'entrée.
        stem = Path(args.input).name.removesuffix(".parquet")
        output = args.output or f"{S3_DIR}/runs/run_{datetime.now():%d%m%Y_%H%M_%S}/{stem}_coded.parquet"
        cache_path = DATA_DIR / "cache" / f"{stem}_coded.cache.jsonl"
        con = connect()
        header, body = read_parquet_input(con, args.input)
        i_label, i_city = (column_index(header, len(header), spec) for spec in (args.col_label or "MAG_DEP", args.col_city or "COM_DEP"))
    else:
        input_path = Path(args.input)
        output = Path(args.output) if args.output else input_path.with_name(input_path.stem + "_coded.csv")
        cache_path = output.with_name(output.name + ".cache.jsonl")
        header, body, i_label, i_city = read_input(input_path, delimiter, args.encoding, args.col_label, args.col_city)
    if args.no_cache:
        cache_path = None
    elif not args.dry_run:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    labels = [r[i_label].strip() for r in body]
    cities = [r[i_city].strip() if i_city is not None else "" for r in body]

    # 1. Passe regex
    regex_cache: dict[str, dict] = {}
    regex_results = []
    for label in labels:
        if label not in regex_cache:
            regex_cache[label] = rules.coder(label, tiers)
        regex_results.append(regex_cache[label])
    status_counts = collections.Counter(r["statut"] for r in regex_results)
    print(f"{len(body)} lignes lues dans {args.input}", file=sys.stderr)
    for status in ("code", "hors_champ", "ambigu", "aucune"):
        print(f"  regex {status:10s} {status_counts[status]:6d}  ({100 * status_counts[status] / max(len(body), 1):.1f} %)", file=sys.stderr)

    # 2. Couples (libellé, commune) à envoyer au LLM, dans l'ordre du fichier
    keys = list(dict.fromkeys(
        (lab, cit) for lab, cit, r in zip(labels, cities, regex_results) if r["statut"] in ("ambigu", "aucune")
    ))
    cache = load_cache(cache_path) if cache_path and not args.dry_run else {}
    to_call = [k for k in keys if k not in cache]
    if args.limit is not None:
        to_call = to_call[: args.limit]
    n_cached = sum(1 for k in keys if k in cache)
    print(f"  à classer par le LLM : {len(keys)} couples (libellé, commune) distincts, dont {n_cached} déjà en cache", file=sys.stderr)

    if args.dry_run:
        n_calls = 0 if args.no_llm else len(to_call)
        print(f"  [dry-run] appels LLM qui seraient faits : {n_calls}", file=sys.stderr)
        return 0

    # 3. Passe LLM
    llm_results = dict(cache)
    if to_call and not args.no_llm:
        provider = resolve_provider(args.search_provider)
        try:
            if not args.no_websearch:
                check_provider(provider)
            from llm.client import get_client
            get_client()  # vérifie LLM_LAB_API_KEY
            if not os.getenv("LLM_LAB_MODEL"):
                raise ValueError("LLM_LAB_MODEL est absent du fichier .env")
        except (ValueError, RuntimeError) as e:
            print(f"Erreur : {e}.", file=sys.stderr)
            return 1
        if args.no_websearch:
            search = "sans recherche web" + (" (--force-search ignoré)" if args.force_search else "")
        else:
            mode = "recherche obligatoire" if args.force_search else "recherche au choix du modèle"
            search = f"{mode}, fournisseur : {provider}"
        log(f"  appels LLM : {len(to_call)} ({search}, {args.workers} en parallèle) ; "
            f"un suivi s'affiche au moins toutes les 30 s")
        llm_results.update(run_llm_pass(to_call, cache_path, nomenclature, provider, max(args.workers, 1), websearch=not args.no_websearch, exemples=exemples,
                                              force_search=args.force_search))

    # 4. Écriture du fichier de sortie
    added_columns = ADDED_COLUMNS + (["trace_json"] if args.trace else [])
    added_rows = []
    for label, city, regex in zip(labels, cities, regex_results):
        llm = None if regex["statut"] in ("code", "hors_champ") else llm_results.get((label, city))
        values = build_output_values(regex, llm, nomenclature, args.trace)
        added_rows.append({c: "" if values[c] is None else str(values[c]) for c in added_columns})
    # Cohérence : libellés équivalents (SAS, pluriel, article…) mais codes différents
    codes = [v["code"] for v in added_rows]
    for values, flag in zip(added_rows, colonne_coherence(labels, codes)):
        values["coherence_codes"] = flag
    divergents = groupes_divergents(labels, codes)
    method_counts = collections.Counter(v["method"] for v in added_rows)
    if parquet:
        write_parquet_output(con, args.input, output, added_rows)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(header + added_columns)
            for row, values in zip(body, added_rows):
                writer.writerow(row + [values[c] for c in added_columns])
    print(f"\nFichier écrit : {output}", file=sys.stderr)
    print("  méthode : " + ", ".join(f"{m} {n}" for m, n in sorted(method_counts.items())), file=sys.stderr)
    if divergents:
        print(f"  cohérence : {len(divergents)} groupe(s) de libellés équivalents aux codes différents "
              f"(colonne coherence_codes) :", file=sys.stderr)
        print("\n".join(resume(divergents, 10)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
