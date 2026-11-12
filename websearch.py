"""
websearch.py : donne un outil de recherche web à ton LLM llm.lab.

PRINCIPE (function calling)
    llm.lab n'a pas d'outil de recherche web. On le fournit nous-mêmes :
      1. On décrit au modèle un outil "web_search" (variable TOOLS).
      2. Si le modèle juge qu'il a besoin du web, il ne répond pas directement :
         il demande d'appeler l'outil avec une requête (un "tool call").
      3. Notre code exécute la recherche (fonction web_search, via SearXNG),
         puis renvoie les résultats au modèle.
      4. Le modèle rédige sa réponse finale en s'appuyant sur ces résultats.
    Cette boucle est dans la fonction run_agent().

STRUCTURE DU FICHIER
    - TOOLS       : la description de l'outil, lue par le modèle
    - web_search  : la vraie recherche (interroge SearXNG)
    - run_agent   : la boucle messages -> outil -> réponse finale

PRÉREQUIS
    - SearXNG doit tourner : ./setup_searxng.sh
      (vérification : curl http://127.0.0.1:8888/healthz doit répondre "OK")
    - Le fichier .env doit contenir LLM_LAB_API_KEY, LLM_LAB_BASE_URL,
      LLM_LAB_MODEL et SEARXNG_URL.

UTILISATION
    Ce module est une bibliothèque, il n'a pas de commande propre. Exemple :
        from websearch import run_agent
        reponse, recherches = run_agent([{"role": "user", "content": "Ta question"}])
    Pour la classification d'enseignes, voir classify.py.
    Quand le modèle fait une recherche, la ligne "[tool] web_search(...)" est
    affichée dans le terminal (sur stderr) ; si elle n'apparaît pas, le modèle
    a répondu sans chercher.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

from client import get_client  # le client OpenAI configuré pour llm.lab (client.py)

# Lit le fichier .env pour remplir les variables d'environnement.
load_dotenv()

# Adresse de SearXNG (valeur par défaut si SEARXNG_URL n'est pas dans le .env).
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")

# Sécurité : nombre maximal de tours "le modèle demande un outil" avant de lui
# imposer de répondre, pour éviter une boucle infinie si le modèle redemande des
# recherches sans fin.
MAX_ITERATIONS = 5

# Description de l'outil, envoyée au modèle à chaque requête. C'est ce texte qui
# permet au modèle de savoir quand et comment utiliser l'outil : la "description"
# est donc importante, tu peux la modifier pour influencer son comportement.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Recherche sur le web des informations récentes ou que tu ne connais pas. "
                "Renvoie une liste de résultats (titre, url, extrait)."
            ),
            # Les arguments que le modèle doit fournir : ici une seule chaîne "query".
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Requête de recherche"},
                },
                "required": ["query"],
            },
        },
    }
]


def web_search(query: str, max_results: int = 5) -> tuple[str, dict]:
    """Interroge SearXNG et renvoie (texte, détails).

      - texte   : les résultats sous forme de texte compact, envoyé au modèle ;
      - détails : un dictionnaire décrivant la recherche (requête, moteurs qui ont
                  répondu, moteurs en échec, résultats retenus avec leur URL), pour
                  garder une trace de ce qui a été fait. Le modèle ne le voit pas.
    """
    details = {
        "requete": query,
        "moteurs_utilises": [],
        "moteurs_en_echec": [],
        "resultats": [],
    }
    try:
        # SearXNG est appelé comme une API web ; format=json demande une réponse JSON.
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()  # lève une erreur si le serveur répond avec un code d'échec
        data = r.json()
    except Exception as e:
        # En cas de souci (SearXNG arrêté, timeout...), on renvoie l'erreur sous forme
        # de texte au modèle plutôt que de faire planter le programme.
        details["erreur"] = str(e)
        return f"Erreur de recherche : {e}", details

    all_results = data.get("results", [])
    results = all_results[:max_results]  # on ne garde que les N premiers

    # SearXNG interroge plusieurs moteurs ; chaque résultat indique ceux qui l'ont
    # trouvé (champ "engines"). On en déduit les moteurs qui ont réellement répondu.
    details["moteurs_utilises"] = sorted(
        {engine for res in all_results for engine in res.get("engines", [])}
    )
    # "unresponsive_engines" : liste de [moteur, raison] (ex. ["duckduckgo", "CAPTCHA"]).
    details["moteurs_en_echec"] = [
        {"moteur": engine, "raison": reason}
        for engine, reason in data.get("unresponsive_engines", [])
    ]
    details["resultats"] = [
        {
            "titre": res.get("title", ""),
            "url": res.get("url", ""),
            "moteurs": res.get("engines", []),
        }
        for res in results
    ]

    if not results:
        return "Aucun résultat.", details
    # Mise en forme : "[1] titre / url / extrait", séparés par une ligne vide.
    text = "\n\n".join(
        f"[{i}] {res.get('title', '')}\n{res.get('url', '')}\n{res.get('content', '')}"
        for i, res in enumerate(results, 1)
    )
    return text, details


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Envoie une conversation au modèle, qui peut utiliser web_search.

    Renvoie un tuple (réponse, recherches) :
      - réponse    : le texte de la réponse finale du modèle ;
      - recherches : la liste des recherches web lancées, chacune sous la forme
                     du dictionnaire "détails" de web_search() (requête, moteurs,
                     résultats...). Liste vide si le modèle a répondu sans chercher.

    `messages` est l'historique au format OpenAI, par exemple
    [{"role": "user", "content": "..."}]. La liste est modifiée en place : les
    demandes d'outil et leurs résultats y sont ajoutés au fil des tours.
    """
    client = get_client()
    model = os.environ["LLM_LAB_MODEL"]
    searches: list[dict] = []  # le détail des recherches effectivement lancées

    for _ in range(MAX_ITERATIONS):
        # On envoie la conversation + la description des outils disponibles.
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS
        )
        message = response.choices[0].message

        # Cas 1 : pas de demande d'outil -> c'est la réponse finale.
        if not message.tool_calls:
            return message.content or "", searches

        # Cas 2 : le modèle demande un ou plusieurs outils. On ajoute sa demande à
        # l'historique, puis on exécute chaque recherche et on ajoute le résultat
        # (role "tool", relié à la demande par son identifiant tool_call_id).
        messages.append(message)
        for call in message.tool_calls:
            args = json.loads(call.function.arguments)  # ex. {"query": "..."}
            print(f"[tool] web_search({args.get('query')!r})", file=sys.stderr)
            text, details = web_search(args.get("query", ""))
            searches.append(details)  # trace pour l'appelant ; le modèle ne la voit pas
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": text}
            )
        # La boucle recommence : le modèle voit maintenant les résultats et peut
        # soit répondre, soit demander une nouvelle recherche.

    # Trop de recherches : on interdit les outils (tool_choice="none") pour forcer
    # le modèle à répondre avec ce qu'il a déjà trouvé.
    response = client.chat.completions.create(
        model=model, messages=messages, tools=TOOLS, tool_choice="none"
    )
    return response.choices[0].message.content or "", searches
