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
         écrit dans <sortie>.cache.jsonl : relancer la commande reprend où elle s'est arrêtée.
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
    error                    message d'erreur si le LLM a échoué
    trace_json               (option --trace) détail complet des recherches web

UTILISATION
    uv run run.py shops.csv                       # écrit shops_coded.csv
    uv run run.py shops.csv --dry-run             # compte les appels LLM, sans en faire
    uv run run.py shops.csv --no-llm              # regex seulement
    uv run run.py shops.csv --limit 5             # au plus 5 appels LLM (pour un essai)
    uv run run.py shops.csv --workers 8 --search-provider serper
    Fichier de la BDF (TSV, latin-1) :
    uv run run.py liste_magasins_a_coder.csv --delimiter '\\t' --encoding latin-1 \\
        --col-label 1 --col-city 2      # (l'en-tête de ce fichier est décalé : colonne 0 = numéro de ligne)

PRÉREQUIS pour les appels LLM : .env rempli (LLM_LAB_*), et fournisseur de recherche disponible
    (SearXNG lancé via ./setup_searxng.sh, ou SERPER_API_KEY renseignée).
"""

import argparse
import collections
import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rules
from classification import DEFAULT_NOMENCLATURE, classify_with_llm, load_nomenclature
from search_providers import PROVIDERS, check_provider, resolve_provider

ADDED_COLUMNS = [
    "code", "store_type", "method",
    "regex_status", "regex_rules", "regex_tier", "regex_candidate_codes",
    "llm_model", "llm_search_provider", "llm_websearch", "llm_search_count", "llm_queries",
    "llm_justification", "llm_tokens", "error",
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

    def column_index(spec: str | None, default: int | None) -> int | None:
        """`spec` = nom de colonne, ou position (0 = première) quand l'en-tête est inutilisable."""
        if spec is None:
            return default if default is not None and default < width else None
        if spec in header:
            return header.index(spec)
        if spec.isdigit() and int(spec) < width:
            return int(spec)
        raise SystemExit(f"Colonne {spec!r} introuvable (en-tête : {header})")

    return header, body, column_index(col_label, 0), column_index(col_city, 1)


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
def run_llm_pass(keys, cache, cache_path, nomenclature, provider, workers) -> dict[tuple[str, str], dict]:
    """Classe les couples (libellé, commune) avec le LLM. Renvoie {clé: résultat} (échecs inclus)."""
    results: dict[tuple[str, str], dict] = {}
    lock = threading.Lock()
    done = 0
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(classify_with_llm, label, city, nomenclature, provider): (label, city) for label, city in keys}
    try:
        with open(cache_path, "a", encoding="utf-8") as cache_file:
            for future in as_completed(futures):
                key = futures[future]
                result = future.result()  # classify_with_llm ne lève pas d'exception
                result["provider"] = provider
                with lock:
                    results[key] = result
                    done += 1
                    if not result["error"]:
                        cache_file.write(json.dumps({"key": list(key), "result": result}, ensure_ascii=False) + "\n")
                        cache_file.flush()
                status = result["code"] or f"ERREUR ({result['error']})"
                web = f"{len(result['searches'])} recherche(s)" if result["searches"] else "sans recherche"
                print(f"[LLM {done}/{len(keys)}] {key[0]!r} ({key[1]!r}) -> {status} ; {web}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterruption : les résultats déjà obtenus sont conservés (reprise possible).", file=sys.stderr)
        executor.shutdown(wait=False, cancel_futures=True)
    else:
        executor.shutdown()
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
    ap.add_argument("input", type=Path, help="fichier CSV : libellé de l'enseigne et commune (ex. shops.csv)")
    ap.add_argument("-o", "--output", type=Path, help="fichier de sortie (défaut : <entrée>_coded.csv)")
    ap.add_argument("--delimiter", default=";", help="séparateur du fichier d'entrée et de sortie (défaut ';' ; '\\t' pour une tabulation)")
    ap.add_argument("--encoding", default="utf-8", help="encodage du fichier d'entrée (défaut utf-8 ; la sortie est en utf-8)")
    ap.add_argument("--col-label", help="colonne du libellé : nom, ou position à partir de 0 (défaut : 1re colonne)")
    ap.add_argument("--col-city", help="colonne de la commune : nom, ou position à partir de 0 (défaut : 2e colonne)")
    ap.add_argument("--nomenclature", type=Path, default=DEFAULT_NOMENCLATURE, help="nomenclature (défaut : nmcl_magasins.csv)")
    ap.add_argument("--tiers", nargs="+", default=["A", "B"], choices=["A", "B"], help="tiers de règles regex utilisés (défaut : A B)")
    ap.add_argument("--no-llm", action="store_true", help="regex seulement : les lignes non codées restent en 'pending'")
    ap.add_argument("--dry-run", action="store_true", help="affiche les comptes et le nombre d'appels LLM prévus, sans rien appeler ni écrire")
    ap.add_argument("--limit", type=int, help="nombre maximal d'appels LLM (couples libellé/commune distincts) ; le reste reste en 'pending'")
    ap.add_argument("--workers", type=int, default=4, help="appels LLM en parallèle (défaut 4)")
    ap.add_argument("--search-provider", choices=list(PROVIDERS), help="fournisseur de recherche web (défaut : SEARCH_PROVIDER du .env, sinon searxng)")
    ap.add_argument("--trace", action="store_true", help="ajoute la colonne trace_json (détail des recherches web)")
    args = ap.parse_args()

    delimiter = "\t" if args.delimiter == "\\t" else args.delimiter
    output = args.output or args.input.with_name(args.input.stem + "_coded.csv")
    cache_path = output.with_name(output.name + ".cache.jsonl")
    tiers = tuple(args.tiers)
    nomenclature = load_nomenclature(args.nomenclature)

    header, body, i_label, i_city = read_input(args.input, delimiter, args.encoding, args.col_label, args.col_city)
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
    cache = load_cache(cache_path) if not args.dry_run else {}
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
            check_provider(provider)
            from client import get_client
            get_client()  # vérifie LLM_LAB_API_KEY
            if not os.getenv("LLM_LAB_MODEL"):
                raise ValueError("LLM_LAB_MODEL est absent du fichier .env")
        except (ValueError, RuntimeError) as e:
            print(f"Erreur : {e}.", file=sys.stderr)
            return 1
        print(f"  appels LLM : {len(to_call)} (fournisseur de recherche : {provider}, {args.workers} en parallèle)", file=sys.stderr)
        llm_results.update(run_llm_pass(to_call, cache, cache_path, nomenclature, provider, max(args.workers, 1)))

    # 4. Écriture du fichier de sortie
    out_header = header + ADDED_COLUMNS + (["trace_json"] if args.trace else [])
    method_counts = collections.Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(out_header)
        for row, label, city, regex in zip(body, labels, cities, regex_results):
            llm = None if regex["statut"] in ("code", "hors_champ") else llm_results.get((label, city))
            values = build_output_values(regex, llm, nomenclature, args.trace)
            method_counts[values["method"]] += 1
            writer.writerow(row + [values[c] for c in ADDED_COLUMNS] + ([values["trace_json"]] if args.trace else []))
    print(f"\nFichier écrit : {output}", file=sys.stderr)
    print("  méthode : " + ", ".join(f"{m} {n}" for m, n in sorted(method_counts.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
