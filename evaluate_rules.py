"""
evaluate_rules.py : contrôle des règles de rules.py.

  1. Sur la liste annotée (Liste_Pointsdevente_BDF2026.csv) : chaque libellé doit être codé
     avec son propre code. On liste les erreurs (mauvais code), les ambigus et les non-codés.
  2. Sur la liste à coder (liste_magasins_a_coder.csv) : couverture, par code, par tier,
     et échantillons pour relire les faux positifs.

UTILISATION
    uv run evaluate_rules.py REFERENCE.csv A_CODER.csv [--tiers A B] [--sortie DOSSIER]
    (--sortie écrit codes.csv, hors_champ.csv, ambigus.csv et non_codes.csv)
                             [--echantillon N] [--voir CODE ...]
    Les fichiers se téléchargent depuis S3 (aws s3 cp ...). Encodage de la liste à coder : latin-1.
"""

import argparse
import collections
import csv
import random
import sys
from pathlib import Path

from rules import REGLES, coder


def lire_reference(chemin: Path) -> list[tuple[str, str, str]]:
    """[(identifiant, code, libellé)] ; ignore les codes absents de la nomenclature."""
    nmcl = {l.split(";")[0].strip() for l in (Path(__file__).parent / "nmcl_magasins.csv").read_text(encoding="utf-8").splitlines()[1:]}
    out = []
    with open(chemin, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            code = row["identifiant"].split("_")[0]
            if code in nmcl:
                out.append((row["identifiant"], code, row["libelle"].strip()))
    return out


def lire_a_coder(chemin: Path) -> list[tuple[str, str]]:
    texte = chemin.read_bytes().decode("latin-1")
    rows = list(csv.reader(texte.splitlines(), delimiter="\t"))[1:]
    return [(r[1].strip(), r[2].strip() if len(r) > 2 else "") for r in rows if len(r) > 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference", type=Path)
    ap.add_argument("a_coder", type=Path)
    ap.add_argument("--tiers", nargs="+", default=["A", "B"])
    ap.add_argument("--sortie", type=Path)
    ap.add_argument("--echantillon", type=int, default=0, help="N libellés distincts tirés par code")
    ap.add_argument("--voir", nargs="*", default=[], help="affiche tous les libellés codés pour ces codes")
    a = ap.parse_args()
    tiers = tuple(a.tiers)
    print(f"{len(REGLES)} règles ({sum(r.tier == 'A' for r in REGLES)} A, {sum(r.tier == 'B' for r in REGLES)} B) ; tiers actifs : {tiers}")

    # 1. Référence
    ref = lire_reference(a.reference)
    faux, ambigus, aucun, ok = [], [], [], 0
    for ident, code, lib in ref:
        r = coder(lib, tiers)
        if r["statut"] in ("code", "hors_champ"):
            if r["code"] == code:
                ok += 1
            else:
                faux.append((ident, lib, r["code"], r["regles"]))
        elif r["statut"] == "ambigu":
            ambigus.append((ident, lib, r["codes_candidats"]))
        else:
            aucun.append((ident, lib))
    print(f"\n== Référence : {len(ref)} libellés : {ok} bien codés, {len(faux)} FAUX (dont hors champ : {sum(1 for x in faux if x[2] == '00')}), {len(ambigus)} ambigus, {len(aucun)} non codés")
    for x in faux:
        print("  FAUX    ", x)
    for x in ambigus:
        print("  AMBIGU  ", x)
    if a.sortie:
        a.sortie.mkdir(parents=True, exist_ok=True)
        (a.sortie / "reference_non_codes.txt").write_text("\n".join(f"{i};{l}" for i, l in aucun), encoding="utf-8")

    # 2. Liste à coder
    lignes = lire_a_coder(a.a_coder)
    cache: dict[str, dict] = {}
    par_code = collections.defaultdict(collections.Counter)  # code -> {libellé: n}
    stats = collections.Counter()
    par_regle = collections.Counter()
    par_hc = collections.Counter()
    resultats = []
    for lib, ville in lignes:
        if lib not in cache:
            cache[lib] = coder(lib, tiers)
        r = cache[lib]
        stats[r["statut"]] += 1
        if r["statut"] == "hors_champ":
            par_hc[lib] += 1
        if r["statut"] == "code":
            stats["tier " + r["tier"]] += 1
            par_code[r["code"]][lib] += 1
            for rid in r["regles"]:
                par_regle[rid] += 1
        resultats.append((lib, ville, r))
    n = len(lignes)
    print(f"\n== À coder : {n} lignes ({len(cache)} libellés distincts)")
    for k in ("code", "tier A", "tier B", "hors_champ", "ambigu", "aucune"):
        print(f"  {k:8s} {stats[k]:6d}  {100 * stats[k] / n:5.1f} %")
    print("  par code :", " ".join(f"{c}:{sum(v.values())}" for c, v in sorted(par_code.items())))
    print("  règles les plus productives :", ", ".join(f"{i}={c}" for i, c in par_regle.most_common(25)))

    rnd = random.Random(0)
    if a.echantillon:
        for code, cnt in sorted(par_code.items()):
            labs = list(cnt)
            pick = rnd.sample(labs, min(a.echantillon, len(labs)))
            print(f"\n-- {code} ({len(labs)} libellés distincts) : " + " | ".join(pick))
    if "00" in a.voir or "HC" in a.voir:
        print("\n-- HORS CHAMP : " + " | ".join(f"{l or '(vide)'} x{c}" for l, c in par_hc.most_common()))
    for code in a.voir:
        print(f"\n-- TOUS {code} : " + " | ".join(f"{l}" + (f" x{c}" if c > 1 else "") + f" [{cache[l]['regles'][0]}]" for l, c in par_code[code].most_common()))

    if a.sortie:
        def ecrire(nom, entete, lignes):
            with open(a.sortie / nom, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(entete)
                w.writerows(lignes)

        ecrire("codes.csv", ["MAG_DEP", "COM_DEP", "code", "regles", "tier"],
               [(lib, ville, r["code"], ",".join(r["regles"]), r["tier"]) for lib, ville, r in resultats if r["statut"] == "code"])
        ecrire("hors_champ.csv", ["MAG_DEP", "COM_DEP", "code", "regles"],
               [(lib, ville, r["code"], ",".join(r["regles"])) for lib, ville, r in resultats if r["statut"] == "hors_champ"])
        ecrire("ambigus.csv", ["MAG_DEP", "COM_DEP", "codes_candidats", "regles"],
               [(lib, ville, ",".join(r["codes_candidats"]), ",".join(r["regles"])) for lib, ville, r in resultats if r["statut"] == "ambigu"])
        ecrire("non_codes.csv", ["MAG_DEP", "COM_DEP"],
               [(lib, ville) for lib, ville, r in resultats if r["statut"] == "aucune"])
        print(f"\nFichiers écrits dans {a.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
