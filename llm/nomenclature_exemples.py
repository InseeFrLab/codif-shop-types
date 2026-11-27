"""
Construit la nomenclature enrichie : chaque code reçoit au plus N exemples de libellés tirés de
la liste annotée (Liste_Pointsdevente_BDF2026.csv). Ces exemples sont injectés dans le prompt du
LLM (voir llm/classification.py) pour qu'il applique les mêmes conventions que les annotateurs.

    uv run -m llm.nomenclature_exemples                       # écrit sur le S3
    uv run -m llm.nomenclature_exemples --output data/nmcl_magasins_exemples.csv --max 10

Sortie : CSV UTF-8 séparé par des ";", colonnes code;libellé;exemples (exemples séparés par " | ").

Choix des exemples (déterministe) : les libellés d'un code sont regroupés par leurs 5 premiers
caractères normalisés (Boucher / Boucherie / Boucherie halal -> un seul groupe). Les groupes les
plus fournis passent en premier (ce sont souvent les types génériques), et l'on garde le libellé
le plus court de chaque groupe.
"""

import argparse
import csv
import io
from difflib import SequenceMatcher

from common.s3 import S3_DIR, connect, read_bytes
from llm.classification import load_nomenclature
from rules.rules import normaliser

REFERENCE = f"{S3_DIR}/Liste_Pointsdevente_BDF2026.csv"
NOMENCLATURE = f"{S3_DIR}/nmcl_magasins.csv"  # nomenclature brute (sans exemples)
OUTPUT = f"{S3_DIR}/nmcl_magasins_exemples.csv"
SEPARATEUR = " | "


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def forme(libelle: str) -> str:
    """Forme de comparaison : normalisée, sans « ET » ni article initial, sans espaces."""
    mots = [m for m in normaliser(libelle).split() if m != "ET"]
    if len(mots) > 1 and mots[0] in ("LE", "LA", "LES", "L"):
        mots = mots[1:]
    return "".join(mots)


def doublon(a: str, b: str) -> bool:
    """Variantes d'un même libellé : H M / H&M, Gamvert / Gammvert, Gemo / Gemo Chaussures."""
    return a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.8


def choisir_exemples(libelles: list[str], n: int) -> list[str]:
    """Au plus n libellés représentatifs (voir l'en-tête du module)."""
    groupes: dict[str, list[str]] = {}  # dict : garde l'ordre d'apparition
    for lib in libelles:
        groupes.setdefault(forme(lib)[:5], []).append(lib)
    tries = sorted(groupes.values(), key=len, reverse=True)  # tri stable : ordre d'apparition à égalité
    choisis: list[str] = []
    for groupe in tries:
        lib = min(groupe, key=len)
        if not any(doublon(forme(lib), forme(c)) for c in choisis):
            choisis.append(lib)
        if len(choisis) == n:
            break
    return choisis


def main() -> None:
    ap = argparse.ArgumentParser(description="Nomenclature enrichie d'exemples annotés.")
    ap.add_argument("--reference", default=REFERENCE, help="liste annotée (défaut : S3)")
    ap.add_argument("--nomenclature", default=NOMENCLATURE, help="nomenclature brute (défaut : nmcl_magasins.csv du S3)")
    ap.add_argument("--output", default=OUTPUT, help="fichier écrit, s3:// ou local (défaut : nmcl_magasins_exemples.csv du S3)")
    ap.add_argument("--max", type=int, default=10, help="nombre maximal d'exemples par code (défaut 10)")
    args = ap.parse_args()

    nomenclature = load_nomenclature(args.nomenclature)
    par_code: dict[str, list[str]] = {}
    ignores = 0
    for row in csv.DictReader(io.StringIO(decode(read_bytes(args.reference)), newline=""), delimiter=";"):
        code, libelle = row["identifiant"].split("_")[0].strip(), row["libelle"].strip()
        if code not in nomenclature:  # 420, 990 : absents de la nomenclature
            ignores += 1
            continue
        if libelle:
            par_code.setdefault(code, []).append(libelle)

    lignes = []
    for ordre, (code, lib) in enumerate(nomenclature.items()):
        exemples = choisir_exemples(par_code.get(code, []), args.max)
        lignes.append((ordre, code, lib, SEPARATEUR.join(exemples)))

    con = connect()
    con.execute('CREATE TABLE nmcl (ordre INTEGER, code VARCHAR, "libellé" VARCHAR, exemples VARCHAR)')
    con.executemany("INSERT INTO nmcl VALUES (?, ?, ?, ?)", lignes)
    con.execute(
        f"""COPY (SELECT code, "libellé", exemples FROM nmcl ORDER BY ordre)
            TO '{args.output}' (FORMAT csv, HEADER, DELIMITER ';')"""
    )
    n_ex = sum(len(e.split(SEPARATEUR)) for *_, e in lignes if e)
    n_codes = sum(1 for *_, e in lignes if e)
    print(f"{len(lignes)} codes, dont {n_codes} avec exemples ; {n_ex} exemples "
          f"({ignores} lignes annotées ignorées : code absent de la nomenclature)")
    print(f"Écrit : {args.output}")


if __name__ == "__main__":
    main()
