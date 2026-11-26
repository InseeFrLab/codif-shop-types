"""
classify.py : classe une enseigne de magasin dans la nomenclature de magasins.

PRINCIPE
    On construit un prompt (modèle dans llm/prompts/classification.txt) contenant la
    nomenclature (nmcl_magasins.csv) et l'enseigne à classer. Le modèle llm.lab
    peut chercher sur le web (outil web_search, voir websearch.py) puis répond
    par un objet JSON {"code": ..., "justification": ...}. On vérifie que le
    code existe dans la nomenclature, on ajoute au résultat si une recherche web
    a été lancée (champ "recherche_web") et on l'affiche.

STRUCTURE DU FICHIER
    - load_nomenclature, build_prompt, parse_answer : voir classification.py
    - main : lecture des arguments, appel du modèle, affichage

    Pour coder tout un fichier (règles regex d'abord, LLM pour le reste), utiliser run.py.

PRÉREQUIS
    - Le .env doit être rempli, et le fournisseur de recherche choisi doit être
      disponible : SearXNG lancé (./common/setup_searxng.sh) ou SERPER_API_KEY renseignée.

UTILISATION
    uv run -m llm.classify "ELECTRO DEPOT" --city 57
    uv run -m llm.classify "AIR BNB"
    uv run -m llm.classify "MK2" --city PARIS --search-provider serper
    Un seul appel à la fois : un nom d'enseigne par exécution.
    Le fournisseur de recherche (searxng ou serper) se choisit avec l'option
    --search-provider ou la variable SEARCH_PROVIDER du .env.
"""

import argparse
import json
import sys

from llm.classification import DEFAULT_NOMENCLATURE, build_prompt, load_nomenclature, parse_answer
from common.search_providers import PROVIDERS, check_provider, resolve_provider
from llm.websearch import run_agent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classe une enseigne de magasin dans la nomenclature."
    )
    parser.add_argument("libelle", help="nom de l'enseigne, ex. \"ELECTRO DEPOT\"")
    parser.add_argument("--city", default="", help="ville (optionnel)")
    parser.add_argument(
        "--nomenclature",
        default=DEFAULT_NOMENCLATURE,
        help="fichier CSV de la nomenclature, s3:// ou local (défaut : nmcl_magasins.csv du S3)",
    )
    parser.add_argument(
        "--search-provider",
        choices=list(PROVIDERS),
        help="fournisseur de recherche web (défaut : variable SEARCH_PROVIDER du "
        ".env, sinon searxng)",
    )
    args = parser.parse_args()

    # On vérifie le fournisseur (nom, clé API) avant d'appeler le modèle, pour
    # échouer tout de suite avec un message clair.
    provider = resolve_provider(args.search_provider)
    try:
        check_provider(provider)
    except ValueError as e:
        print(f"Erreur : {e}.", file=sys.stderr)
        return 1

    nomenclature = load_nomenclature(args.nomenclature)
    prompt = build_prompt(args.libelle, args.city, nomenclature)
    answer, searches = run_agent([{"role": "user", "content": prompt}], provider)

    try:
        result = parse_answer(answer, nomenclature)
    except ValueError as e:
        print(f"Erreur : {e}.\nRéponse brute du modèle :\n{answer}", file=sys.stderr)
        return 1

    # Indique le fournisseur de recherche, si le modèle a lancé une recherche web et
    # détaille chaque recherche (requête, moteurs utilisés ou en échec, URLs des
    # résultats retenus).
    result["recherche_web"] = {
        "fournisseur": provider,
        "lancee": bool(searches),
        "recherches": searches,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
