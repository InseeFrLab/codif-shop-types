"""
search_providers.py : les fournisseurs de recherche web utilisables par websearch.py.

PRINCIPE
    Chaque fournisseur est une fonction search_xxx(query, max_results) qui
    interroge un service de recherche et renvoie TOUJOURS le même format :

        {
          "resultats": [{"titre", "url", "extrait", "moteurs": [...]}],
          "moteurs_utilises": [...],                       # moteurs qui ont répondu
          "moteurs_en_echec": [{"moteur", "raison"}],      # moteurs en échec
        }

    En cas d'erreur (réseau, clé invalide...), la fonction lève une exception :
    c'est websearch.web_search() qui l'attrape.

FOURNISSEURS
    - searxng : méta-moteur installé en local (./setup_searxng.sh), gratuit,
                interroge plusieurs moteurs mais ceux-ci peuvent te bloquer.
    - serper  : API payante (https://serper.dev), résultats de Google
                uniquement, plus fiable. Nécessite SERPER_API_KEY dans le .env.

CHOIX DU FOURNISSEUR (par ordre de priorité)
    1. l'option --search-provider de classify.py
    2. la variable SEARCH_PROVIDER du .env
    3. "searxng" par défaut

AJOUTER UN FOURNISSEUR
    Écrire une fonction search_xxx au format ci-dessus, l'ajouter au dictionnaire
    PROVIDERS en bas du fichier (et, si besoin, ses prérequis dans check_provider).
"""

import os

import requests
from dotenv import load_dotenv

# Lit le fichier .env pour remplir les variables d'environnement.
load_dotenv()

DEFAULT_PROVIDER = "searxng"


def search_searxng(query: str, max_results: int) -> dict:
    """Interroge l'instance SearXNG locale (adresse : SEARXNG_URL dans le .env)."""
    url = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")
    # SearXNG est appelé comme une API web ; format=json demande une réponse JSON.
    r = requests.get(
        f"{url}/search",
        params={"q": query, "format": "json"},
        timeout=15,
    )
    r.raise_for_status()  # lève une erreur si le serveur répond avec un code d'échec
    data = r.json()

    all_results = data.get("results", [])

    return {
        # On ne garde que les N premiers résultats.
        "resultats": [
            {
                "titre": res.get("title", ""),
                "url": res.get("url", ""),
                "extrait": res.get("content", ""),
                "moteurs": res.get("engines", []),
            }
            for res in all_results[:max_results]
        ],
        # SearXNG interroge plusieurs moteurs ; chaque résultat indique ceux qui l'ont
        # trouvé (champ "engines"). On en déduit les moteurs qui ont réellement répondu.
        "moteurs_utilises": sorted(
            {engine for res in all_results for engine in res.get("engines", [])}
        ),
        # "unresponsive_engines" : liste de [moteur, raison] (ex. ["duckduckgo", "CAPTCHA"]).
        "moteurs_en_echec": [
            {"moteur": engine, "raison": reason}
            for engine, reason in data.get("unresponsive_engines", [])
        ],
    }


def search_serper(query: str, max_results: int) -> dict:
    """Interroge l'API Serper (résultats Google). Clé : SERPER_API_KEY dans le .env."""
    r = requests.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": os.environ["SERPER_API_KEY"],
            "Content-Type": "application/json",
        },
        json={
            "q": query,
            "num": max_results,
            # Pays et langue des résultats : France / français par défaut, car les
            # enseignes sont françaises. Modifiable via SERPER_GL / SERPER_HL.
            "gl": os.getenv("SERPER_GL", "fr"),
            "hl": os.getenv("SERPER_HL", "fr"),
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()

    # "organic" contient les résultats de recherche classiques (hors encadrés).
    organic = data.get("organic", [])[:max_results]

    out = {
        "resultats": [
            {
                "titre": res.get("title", ""),
                "url": res.get("link", ""),
                "extrait": res.get("snippet", ""),
                "moteurs": ["google"],  # Serper ne renvoie que des résultats Google
            }
            for res in organic
        ],
        "moteurs_utilises": ["google"] if organic else [],
        "moteurs_en_echec": [],
    }
    # Nombre de crédits Serper consommés par cette requête (suivi du budget).
    if "credits" in data:
        out["credits"] = data["credits"]
    return out


# Les fournisseurs disponibles : nom -> fonction de recherche.
PROVIDERS = {
    "searxng": search_searxng,
    "serper": search_serper,
}


def resolve_provider(cli_value: str | None = None) -> str:
    """Détermine le fournisseur : option CLI, sinon SEARCH_PROVIDER, sinon searxng."""
    return (cli_value or os.getenv("SEARCH_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def check_provider(name: str) -> None:
    """Vérifie que le fournisseur existe et que ses prérequis sont là.

    Lève ValueError avec un message clair sinon, pour échouer tout de suite plutôt
    qu'après un appel au modèle.
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"fournisseur de recherche inconnu : {name!r} "
            f"(choix possibles : {', '.join(PROVIDERS)})"
        )
    if name == "serper" and not os.getenv("SERPER_API_KEY"):
        raise ValueError("SERPER_API_KEY est absente ou vide dans le .env")
