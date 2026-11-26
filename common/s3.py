"""Accès au S3 du SSP Cloud avec DuckDB (identifiants lus dans les variables AWS_*)."""

import os

import duckdb

S3_DIR = "s3://projet-budget-famille/data/shop-types-classification"


def connect() -> duckdb.DuckDBPyConnection:
    """Connexion DuckDB capable de lire et d'écrire sur le S3."""
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "CREATE SECRET (TYPE S3, KEY_ID ?, SECRET ?, SESSION_TOKEN ?, REGION ?, ENDPOINT ?, URL_STYLE 'path')",
        [
            os.environ["AWS_ACCESS_KEY_ID"],
            os.environ["AWS_SECRET_ACCESS_KEY"],
            os.environ.get("AWS_SESSION_TOKEN", ""),
            os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            os.environ["AWS_ENDPOINT_URL"].removeprefix("https://").rstrip("/"),
        ],
    )
    return con


def read_bytes(path: str) -> bytes:
    """Contenu brut d'un fichier, sur le S3 (s3://...) ou en local."""
    if not path.startswith("s3://"):
        with open(path, "rb") as f:
            return f.read()
    return connect().execute("SELECT content FROM read_blob(?)", [path]).fetchone()[0]
