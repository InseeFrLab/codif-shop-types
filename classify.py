"""
classify.py : classe une enseigne de magasin dans la nomenclature de magasins.

PRINCIPE
    On construit un prompt (modèle dans prompts/classification.txt) contenant la
    nomenclature (nmcl_magasins.csv) et l'enseigne à classer. Le modèle llm.lab
    peut chercher sur le web (outil web_search, voir websearch.py) puis répond
    par un objet JSON {"code": ..., "justification": ...}. On vérifie que le
    code existe dans la nomenclature, on ajoute au résultat si une recherche web
    a été lancée (champ "recherche_web") et on l'affiche.

STRUCTURE DU FICHIER
    - load_nomenclature : lit nmcl_magasins.csv -> {code: libellé}
    - build_prompt      : remplit le modèle de prompt (fichier texte à part)
    - parse_answer      : extrait et valide le JSON renvoyé par le modèle
    - main              : lecture des arguments, appel du modèle, affichage

PRÉREQUIS
    - SearXNG doit tourner (./setup_searxng.sh) et le .env doit être rempli.

UTILISATION
    uv run classify.py "ELECTRO DEPOT" --city 57
    uv run classify.py "AIR BNB"
    Un seul appel à la fois : un nom d'enseigne par exécution.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from string import Template

from websearch import run_agent

# Les chemins sont relatifs à ce fichier, pour fonctionner quel que soit le
# dossier depuis lequel on lance la commande.
BASE_DIR = Path(__file__).parent
PROMPT_PATH = BASE_DIR / "prompts" / "classification.txt"
DEFAULT_NOMENCLATURE = BASE_DIR / "nmcl_magasins.csv"


def load_nomenclature(path: Path) -> dict[str, str]:
    """Lit la nomenclature (CSV séparé par des ";") et renvoie {code: libellé}."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        # strip() : certains libellés ont un espace parasite en tête (ex. code 421).
        return {row["code"].strip(): row["libellé"].strip() for row in reader}


def build_prompt(libelle: str, ville: str, nomenclature: dict[str, str]) -> str:
    """Remplit le modèle de prompt avec la nomenclature et l'enseigne à classer.

    Le modèle utilise la syntaxe string.Template ($nomenclature, $libelle, $ville)
    plutôt que {} car le prompt contient lui-même des accolades (exemple de JSON).
    """
    template = Template(PROMPT_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        nomenclature="\n".join(f"{code};{lib}" for code, lib in nomenclature.items()),
        libelle=libelle,
        ville=ville or "(non renseignée)",
    )


def parse_answer(text: str, nomenclature: dict[str, str]) -> dict:
    """Extrait le JSON de la réponse du modèle et vérifie que le code existe.

    Lève ValueError (avec un message clair) si la réponse est inexploitable.
    """
    # Le modèle peut entourer le JSON de texte ou d'un bloc ```json : on prend
    # simplement ce qui se trouve entre la première "{" et la dernière "}".
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("aucun objet JSON dans la réponse du modèle")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalide dans la réponse du modèle ({e})") from e

    code = str(data.get("code", "")).strip()
    if code not in nomenclature:
        raise ValueError(f"le code {code!r} n'existe pas dans la nomenclature")

    return {
        "code": code,
        "libelle": nomenclature[code],  # recopié depuis la nomenclature, pas du modèle
        "justification": data.get("justification", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classe une enseigne de magasin dans la nomenclature."
    )
    parser.add_argument("libelle", help="nom de l'enseigne, ex. \"ELECTRO DEPOT\"")
    parser.add_argument("--city", default="", help="ville (optionnel)")
    parser.add_argument(
        "--nomenclature",
        type=Path,
        default=DEFAULT_NOMENCLATURE,
        help="fichier CSV de la nomenclature (défaut : nmcl_magasins.csv)",
    )
    args = parser.parse_args()

    nomenclature = load_nomenclature(args.nomenclature)
    prompt = build_prompt(args.libelle, args.city, nomenclature)
    answer, queries = run_agent([{"role": "user", "content": prompt}])

    try:
        result = parse_answer(answer, nomenclature)
    except ValueError as e:
        print(f"Erreur : {e}.\nRéponse brute du modèle :\n{answer}", file=sys.stderr)
        return 1

    # Indique si le modèle a lancé une recherche web, et avec quelles requêtes.
    result["recherche_web"] = {"lancee": bool(queries), "requetes": queries}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
