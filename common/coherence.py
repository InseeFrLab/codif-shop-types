"""
Contrôle de cohérence des codes : un même libellé, écrit de façons différentes, doit recevoir
le même code.

Les libellés sont regroupés par une clé normalisée : majuscules sans accents (rules.normaliser),
sans forme juridique (SAS, SA, SNC, SARL…), sans article initial (LE, LA, LES…) et au singulier.
Ainsi « LES VERGERS DE VENDEE » et « VERGER DE VENDEE », ou « SAS RUE DES PRODUCTEURS » et
« RUE DES PRODUCTEURS », tombent dans le même groupe. Un groupe dont les lignes ont des codes
différents est signalé : c'est soit une erreur, soit deux établissements homonymes (la commune
n'entre pas dans la clé), à vérifier dans les deux cas.

run.py l'applique à la fin de chaque run (colonne coherence_codes). Sur un fichier déjà codé :

    uv run -m common.coherence <fichier_coded.parquet|csv> [--col-label MAG_DEP] [--col-code code]
"""

import argparse
from collections import Counter, defaultdict

from rules.rules import normaliser

FORMES_JURIDIQUES = {"SAS", "SASU", "SA", "SARL", "SNC", "EURL", "EARL", "SCI", "SCEA", "GAEC", "SC",
                     "STE", "SOCIETE", "ETS", "ETABLISSEMENTS"}
ARTICLES = {"LE", "LA", "LES", "L", "AU", "AUX"}


def cle(libelle: str) -> str:
    """Clé de regroupement d'un libellé ("" si rien ne reste)."""
    mots = [m for m in normaliser(libelle or "").split() if m not in FORMES_JURIDIQUES]
    while mots and mots[0] in ARTICLES:
        mots = mots[1:]
    # Singulier grossier : VERGERS -> VERGER, CHEVAUX -> CHEVAU (appliqué à tous, donc cohérent).
    return " ".join(m[:-1] if len(m) > 3 and m[-1] in "SX" else m for m in mots)


def groupes_divergents(libelles: list[str], codes: list[str]) -> dict[str, dict]:
    """{clé: {"codes": Counter(code), "libelles": Counter(libellé)}} pour les groupes aux codes divergents.

    Les lignes sans code (pending, error) sont ignorées.
    """
    groupes: dict[str, dict] = defaultdict(lambda: {"codes": Counter(), "libelles": Counter()})
    for lib, code in zip(libelles, codes):
        k = cle(lib)
        if k and code:
            groupes[k]["codes"][code] += 1
            groupes[k]["libelles"][lib] += 1
    return {k: g for k, g in groupes.items() if len(g["codes"]) > 1}


def colonne_coherence(libelles: list[str], codes: list[str]) -> list[str]:
    """Pour chaque ligne : les codes du groupe (« 129,323 ») s'il est divergent, sinon ""."""
    div = groupes_divergents(libelles, codes)
    return [",".join(sorted(div[k]["codes"])) if (k := cle(lib)) in div else "" for lib in libelles]


def resume(div: dict[str, dict], limite: int = 20) -> list[str]:
    """Lignes de texte lisibles, groupes les plus fournis d'abord."""
    lignes = []
    for k, g in sorted(div.items(), key=lambda kv: -sum(kv[1]["codes"].values()))[:limite]:
        codes = ", ".join(f"{c} ×{n}" for c, n in g["codes"].most_common())
        libs = " / ".join(g["libelles"])
        lignes.append(f"  {k!r} : {codes}  ({libs})")
    if len(div) > limite:
        lignes.append(f"  … et {len(div) - limite} autre(s) groupe(s)")
    return lignes


def main() -> None:
    ap = argparse.ArgumentParser(description="Signale les libellés équivalents qui ont reçu des codes différents.")
    ap.add_argument("fichier", help="fichier codé par run.py : Parquet ou CSV, s3:// ou local")
    ap.add_argument("--col-label", default="MAG_DEP", help="colonne du libellé (défaut MAG_DEP ; libelle pour un CSV)")
    ap.add_argument("--col-code", default="code", help="colonne du code (défaut code)")
    ap.add_argument("--limite", type=int, default=50, help="nombre maximal de groupes affichés (défaut 50)")
    args = ap.parse_args()

    from common.s3 import connect  # import tardif : seul le CLI lit des fichiers
    rows = connect().execute(
        f'SELECT CAST("{args.col_label}" AS VARCHAR), CAST("{args.col_code}" AS VARCHAR) FROM \'{args.fichier}\''
    ).fetchall()
    libelles, codes = [r[0] or "" for r in rows], [r[1] or "" for r in rows]
    div = groupes_divergents(libelles, codes)
    n_lignes = sum(sum(g["codes"].values()) for g in div.values())
    print(f"{len(rows)} lignes ; {len(div)} groupe(s) de libellés aux codes divergents ({n_lignes} lignes)")
    print("\n".join(resume(div, args.limite)))


if __name__ == "__main__":
    main()
