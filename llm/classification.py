"""
classification.py : classification d'une enseigne par le LLM (avec recherche web).

Brique réutilisable par run.py (traitement d'un fichier) et classify.py (une enseigne) :
    - load_nomenclature : lit la nomenclature (sur le S3 par défaut) -> {code: libellé} ;
                          chargée une fois par run, puis injectée dans chaque prompt
    - load_examples     : lit les exemples annotés de la nomenclature enrichie -> {code: [exemples]}
                          (fichier construit par llm/nomenclature_exemples.py)
    - build_prompt      : remplit le modèle de prompt (llm/prompts/classification.txt)
    - parse_answer      : extrait et valide le JSON renvoyé par le modèle
    - classify_with_llm : enchaîne les trois et renvoie un résultat structuré,
                          sans jamais lever d'exception (les erreurs sont dans le résultat)
"""

import csv
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from string import Template

from common.s3 import S3_DIR, read_bytes
from llm.websearch import run_agent

# Les chemins sont relatifs à ce fichier, pour fonctionner quel que soit le
# dossier depuis lequel on lance la commande.
BASE_DIR = Path(__file__).parent
PROMPT_PATH = BASE_DIR / "prompts" / "classification.txt"
# Nomenclature enrichie : code;libellé;exemples (voir llm/nomenclature_exemples.py).
DEFAULT_NOMENCLATURE = f"{S3_DIR}/nmcl_magasins_exemples.csv"
SEPARATEUR_EXEMPLES = " | "
CONFIANCES = ("haute", "moyenne", "basse")

# Consignes d'usage de la recherche web, selon le mode (variable $consignes_recherche du prompt).
CONSIGNES_RECHERCHE = {
    True: "- Fais toujours au moins une recherche avant de répondre, même si tu crois connaître l'enseigne "
          "ou si l'activité te semble évidente (3 recherches au maximum).",
    False: "- Si tu ne connais pas l'enseigne avec certitude, ou si le nom est ambigu, utilise l'outil "
           "web_search (3 recherches au maximum).\n"
           "- Si l'activité est évidente à partir du nom, ne fais pas de recherche.",
}


def _read_rows(path: str | Path) -> list[dict]:
    """Lignes d'un CSV séparé par des ";" (S3 ou local), en UTF-8 ou, à défaut, en cp1252."""
    raw = read_bytes(str(path))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))


def load_nomenclature(path: str | Path = DEFAULT_NOMENCLATURE) -> dict[str, str]:
    """Lit la nomenclature (brute ou enrichie) -> {code: libellé}. La colonne exemples est ignorée."""
    # strip() : certains libellés ont un espace parasite en tête (ex. code 421).
    return {row["code"].strip(): row["libellé"].strip() for row in _read_rows(path)}


def load_examples(path: str | Path = DEFAULT_NOMENCLATURE) -> dict[str, list[str]]:
    """Exemples annotés par code -> {code: [exemples]} ; {} si le fichier n'a pas de colonne exemples."""
    return {
        row["code"].strip(): row["exemples"].split(SEPARATEUR_EXEMPLES)
        for row in _read_rows(path)
        if (row.get("exemples") or "").strip()
    }


def build_prompt(
    libelle: str, ville: str, nomenclature: dict[str, str], exemples: dict[str, list[str]] | None = None,
    force_search: bool = False,
) -> str:
    """Remplit le modèle de prompt avec la nomenclature (et ses exemples annotés) et l'enseigne à classer.

    Le modèle utilise la syntaxe string.Template ($nomenclature, $libelle, $ville)
    plutôt que {} car le prompt contient lui-même des accolades (exemple de JSON).
    """
    exemples = exemples or {}
    lignes = []
    for code, lib in nomenclature.items():
        ligne = f"{code};{lib}"
        if exemples.get(code):
            ligne += f" (exemples annotés : {', '.join(exemples[code])})"
        lignes.append(ligne)
    template = Template(PROMPT_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        nomenclature="\n".join(lignes),
        libelle=libelle,
        consignes_recherche=CONSIGNES_RECHERCHE[force_search],
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
        "confiance": normalize_confidence(data.get("confiance")),
        "justification": data.get("justification", ""),
    }


def normalize_confidence(value) -> str:
    """'Haute', ' MOYENNE ', 'basse' -> haute/moyenne/basse ; toute autre valeur -> "".

    Une confiance absente ou invalide ne fait pas échouer la ligne : le code reste exploitable.
    """
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().strip().lower()
    return text if text in CONFIANCES else ""


def classify_with_llm(
    label: str, city: str, nomenclature: dict[str, str], provider: str | None = None,
    websearch: bool = True, exemples: dict[str, list[str]] | None = None, force_search: bool = False,
) -> dict:
    """Classe une enseigne avec le LLM (et la recherche web si le modèle la juge utile).

    Renvoie un dictionnaire sérialisable en JSON :
      - code, confidence, justification : le résultat (code None en cas d'échec ; confidence =
                              haute/moyenne/basse, "" si le modèle ne l'a pas donnée) ;
      - searches            : détail des recherches web lancées (liste vide si aucune) ;
      - calls, tokens       : nombre d'appels au modèle et tokens consommés ;
      - model, provider     : modèle LLM et fournisseur de recherche utilisés ;
      - error, raw_answer   : message d'erreur et réponse brute du modèle en cas d'échec.
    """
    result = {
        "code": None,
        "confidence": "",
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
        force_search = force_search and websearch  # sans outil, pas de recherche à imposer
        prompt = build_prompt(label, city, nomenclature, exemples, force_search)
        answer, searches = run_agent(
            [{"role": "user", "content": prompt}], provider, stats=stats, verbose=False,
            websearch=websearch, force_search=force_search,
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
    result["confidence"] = parsed["confiance"]
    result["justification"] = parsed["justification"]
    return result
