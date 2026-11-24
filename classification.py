"""
classification.py : classification d'une enseigne par le LLM (avec recherche web).

Brique réutilisable par run.py (traitement d'un fichier) et classify.py (une enseigne) :
    - load_nomenclature : lit nmcl_magasins.csv -> {code: libellé}
    - build_prompt      : remplit le modèle de prompt (prompts/classification.txt)
    - parse_answer      : extrait et valide le JSON renvoyé par le modèle
    - classify_with_llm : enchaîne les trois et renvoie un résultat structuré,
                          sans jamais lever d'exception (les erreurs sont dans le résultat)
"""

import csv
import json
import os
import re
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


def classify_with_llm(
    label: str, city: str, nomenclature: dict[str, str], provider: str | None = None
) -> dict:
    """Classe une enseigne avec le LLM (et la recherche web si le modèle la juge utile).

    Renvoie un dictionnaire sérialisable en JSON :
      - code, justification : le résultat (code None en cas d'échec) ;
      - searches            : détail des recherches web lancées (liste vide si aucune) ;
      - calls, tokens       : nombre d'appels au modèle et tokens consommés ;
      - model, provider     : modèle LLM et fournisseur de recherche utilisés ;
      - error, raw_answer   : message d'erreur et réponse brute du modèle en cas d'échec.
    """
    result = {
        "code": None,
        "justification": "",
        "searches": [],
        "calls": 0,
        "tokens": 0,
        "model": os.getenv("LLM_LAB_MODEL", ""),
        "provider": provider or "",
        "error": None,
        "raw_answer": None,
    }
    stats: dict = {}
    try:
        prompt = build_prompt(label, city, nomenclature)
        answer, searches = run_agent(
            [{"role": "user", "content": prompt}], provider, stats=stats, verbose=False
        )
    except Exception as e:  # réseau, clé invalide, fournisseur de recherche, etc.
        result["error"] = f"{type(e).__name__}: {e}"
        result["calls"], result["tokens"] = stats.get("calls", 0), stats.get("tokens", 0)
        return result

    result["searches"] = searches
    result["calls"], result["tokens"] = stats.get("calls", 0), stats.get("tokens", 0)
    result["raw_answer"] = answer
    try:
        parsed = parse_answer(answer, nomenclature)
    except ValueError as e:
        result["error"] = str(e)
        return result
    result["code"] = parsed["code"]
    result["justification"] = parsed["justification"]
    return result
