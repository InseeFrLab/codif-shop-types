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
    Cette boucle est dans la fonction chat().

STRUCTURE DU FICHIER
    - TOOLS       : la description de l'outil, lue par le modèle
    - web_search  : la vraie recherche (interroge SearXNG)
    - chat        : la boucle question -> outil -> réponse
    - bloc final  : permet de lancer le fichier depuis le terminal

PRÉREQUIS
    - SearXNG doit tourner : ./setup_searxng.sh
      (vérification : curl http://127.0.0.1:8888/healthz doit répondre "OK")
    - Le fichier .env doit contenir LLM_LAB_API_KEY, LLM_LAB_BASE_URL,
      LLM_LAB_MODEL et SEARXNG_URL.

UTILISATION
    En terminal :
        uv run websearch.py "Quelle est la météo à Paris ?"
        (sans question, une question d'exemple est utilisée)
    Dans un autre script Python :
        from websearch import chat
        print(chat("Ta question"))
    Quand le modèle fait une recherche, la ligne "[tool] web_search(...)" est
    affichée dans le terminal ; si elle n'apparaît pas, le modèle a répondu
    sans chercher.
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

# Sécurité : nombre maximal de tours "le modèle demande un outil" avant d'abandonner,
# pour éviter une boucle infinie si le modèle redemande des recherches sans fin.
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


def web_search(query: str, max_results: int = 5) -> str:
    """Interroge SearXNG et renvoie les résultats sous forme de texte compact."""
    try:
        # SearXNG est appelé comme une API web ; format=json demande une réponse JSON.
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()  # lève une erreur si le serveur répond avec un code d'échec
        results = r.json().get("results", [])[:max_results]  # on garde les N premiers
    except Exception as e:
        # En cas de souci (SearXNG arrêté, timeout...), on renvoie l'erreur sous forme
        # de texte au modèle plutôt que de faire planter le programme.
        return f"Erreur de recherche : {e}"

    if not results:
        return "Aucun résultat."
    # Mise en forme : "[1] titre / url / extrait", séparés par une ligne vide.
    return "\n\n".join(
        f"[{i}] {res.get('title', '')}\n{res.get('url', '')}\n{res.get('content', '')}"
        for i, res in enumerate(results, 1)
    )


def chat(question: str) -> str:
    """Pose une question au modèle, qui peut utiliser web_search, et renvoie sa réponse."""
    client = get_client()
    model = os.environ["LLM_LAB_MODEL"]
    # "messages" est l'historique de la conversation envoyé au modèle à chaque appel.
    messages = [{"role": "user", "content": question}]

    for _ in range(MAX_ITERATIONS):
        # On envoie la conversation + la description des outils disponibles.
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS
        )
        message = response.choices[0].message

        # Cas 1 : pas de demande d'outil -> c'est la réponse finale.
        if not message.tool_calls:
            return message.content

        # Cas 2 : le modèle demande un ou plusieurs outils. On ajoute sa demande à
        # l'historique, puis on exécute chaque recherche et on ajoute le résultat
        # (role "tool", relié à la demande par son identifiant tool_call_id).
        messages.append(message)
        for call in message.tool_calls:
            args = json.loads(call.function.arguments)  # ex. {"query": "..."}
            print(f"[tool] web_search({args.get('query')!r})", file=sys.stderr)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": web_search(args.get("query", "")),
                }
            )
        # La boucle recommence : le modèle voit maintenant les résultats et peut
        # soit répondre, soit demander une nouvelle recherche.

    return "Nombre maximal d'appels d'outils atteint."


# Ce bloc ne s'exécute que si on lance le fichier directement (python websearch.py ...),
# pas quand il est importé depuis un autre script.
if __name__ == "__main__":
    # La question est tout ce qui suit le nom du fichier dans la commande.
    question = " ".join(sys.argv[1:]) or "Quelle est la dernière version de Python ?"
    print(chat(question))
