"""
Tire un échantillon aléatoire reproductible de la liste des magasins à coder, et l'enregistre
en Parquet sur le S3.

    uv run -m sampling.draw_sample                      # 100 lignes, graine 42
    uv run -m sampling.draw_sample --size 500 --seed 7

Sortie : s3://.../samples/liste_magasins_a_coder_n<taille>_seed<graine>_<JJMMAAAA>.parquet

Reproductible : chaque ligne est classée par md5(graine + ID), on garde les premières. Même
graine => même échantillon, quelle que soit la machine. Pour une même graine, l'échantillon
de 100 est inclus dans celui de 200.

Les identifiants S3 sont lus dans les variables AWS_* du SSP Cloud.
"""

import argparse
from datetime import date

from common.s3 import S3_DIR, connect

SOURCE = f"{S3_DIR}/liste_magasins_a_coder.parquet"


def main() -> None:
    ap = argparse.ArgumentParser(description="Tirage aléatoire reproductible des magasins à coder.")
    ap.add_argument("--size", type=int, default=100, help="nombre de lignes tirées (défaut 100)")
    ap.add_argument("--seed", type=int, default=42, help="graine du tirage (défaut 42)")
    ap.add_argument("--output", help="fichier de sortie (défaut : dossier samples/ du S3)")
    args = ap.parse_args()

    output = args.output or f"{S3_DIR}/samples/liste_magasins_a_coder_n{args.size}_seed{args.seed}_{date.today():%d%m%Y}.parquet"
    con = connect()
    con.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet('{SOURCE}')
            ORDER BY md5(CAST(? AS VARCHAR) || '-' || CAST(ID AS VARCHAR))
            LIMIT ?
        ) TO '{output}' (FORMAT parquet)
        """,
        [args.seed, args.size],
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{output}')").fetchone()[0]
    print(f"{n} lignes écrites dans {output}")


if __name__ == "__main__":
    main()
