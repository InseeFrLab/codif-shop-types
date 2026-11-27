"""
websearch.py : donne un outil de recherche web à ton LLM llm.lab.

PRINCIPE (function calling)
    llm.lab n'a pas d'outil de recherche web. On le fournit nous-mêmes :
      1. On décrit au modèle un outil "web_search" (variable TOOLS).
      2. Si le modèle juge qu'il a besoin du web, il ne répond pas directement :
         il demande d'appeler l'outil avec une requête (un "tool call").
      3. Notre code exécute la recherche (fonction web_search), avec le
         fournisseur choisi (SearXNG ou Serper, voir search_providers.py),
         puis renvoie les résultats au modèle.
      4. Le modèle rédige sa réponse finale en s'appuyant sur ces résultats.
    Cette boucle est dans la fonction run_agent().

STRUCTURE DU FICHIER
    - TOOLS       : la description de l'outil, lue par le modèle
    - web_search  : la recherche : appelle le fournisseur et met en forme
    - run_agent   : la boucle messages -> outil -> réponse finale

PRÉREQUIS
    - Le fichier .env doit contenir LLM_LAB_API_KEY, LLM_LAB_BASE_URL et
      LLM_LAB_MODEL.
    - Selon le fournisseur de recherche (variable SEARCH_PROVIDER du .env) :
        * searxng : SearXNG doit tourner (./common/setup_searxng.sh ; vérification :
          curl http://127.0.0.1:8888/healthz doit répondre "OK") ;
        * serper  : SERPER_API_KEY doit être renseignée dans le .env.

UTILISATION
    Ce module est une bibliothèque, il n'a pas de commande propre. Exemple :
        from websearch import run_agent
        reponse, recherches = run_agent(
            [{"role": "user", "content": "Ta question"}],
            provider="serper",  # optionnel : sinon SEARCH_PROVIDER, sinon searxng
        )
    Pour la classification d'enseignes, voir classify.py.
    Quand le modèle fait une recherche, la ligne "[tool] web_search(...)" est
    affichée dans le terminal (sur stderr) ; si elle n'apparaît pas, le modèle
    a répondu sans chercher.
"""

import json
import os
import sys

from dotenv import load_dotenv
from openai import BadRequestError

from llm.client import get_client  # le client OpenAI configuré pour llm.lab (client.py)
from common.search_providers import PROVIDERS, check_provider, resolve_provider

# Lit le fichier .env pour remplir les variables d'environnement.
load_dotenv()

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
                    "query": {
                        "type": "string",
                        "description": (
                            "Requête de recherche : le nom exact de l'enseigne suivi de "
                            "la ville, sans mot supplémentaire (pas de « activité », "
                            "« magasin », « entreprise »...). Exemple : MK2 Paris"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }
]


def web_search(
    query: str, provider: str | None = None, max_results: int = 5
) -> tuple[str, dict]:
    """Lance une recherche web et renvoie (texte, détails).

      - texte   : les résultats sous forme de texte compact, envoyé au modèle ;
      - détails : un dictionnaire décrivant la recherche (requête, moteurs qui ont
                  répondu, moteurs en échec, résultats retenus avec leur URL), pour
                  garder une trace de ce qui a été fait. Le modèle ne le voit pas.

    `provider` : "searxng" ou "serper" (sinon SEARCH_PROVIDER, sinon "searxng").
    """
    provider = resolve_provider(provider)
    details = {
        "requete": query,
        "moteurs_utilises": [],
        "moteurs_en_echec": [],
        "resultats": [],
    }
    try:
        # Le fournisseur renvoie toujours le même format (voir search_providers.py).
        found = PROVIDERS[provider](query, max_results)
    except Exception as e:
        # En cas de souci (service arrêté, timeout, clé invalide...), on renvoie
        # l'erreur sous forme de texte au modèle plutôt que de faire planter le
        # programme.
        details["erreur"] = str(e)
        return f"Erreur de recherche : {e}", details

    results = found["resultats"]
    details["moteurs_utilises"] = found["moteurs_utilises"]
    details["moteurs_en_echec"] = found["moteurs_en_echec"]
    if "credits" in found:  # fournisseurs payants : crédits consommés
        details["credits"] = found["credits"]
    details["resultats"] = [
        {"titre": res["titre"], "url": res["url"], "moteurs": res["moteurs"]}
        for res in results
    ]

    if not results:
        return "Aucun résultat.", details
    # Mise en forme : "[1] titre / url / extrait", séparés par une ligne vide.
    text = "\n\n".join(
        f"[{i}] {res['titre']}\n{res['url']}\n{res['extrait']}"
        for i, res in enumerate(results, 1)
    )
    return text, details


def _record_usage(stats: dict | None, response) -> None:
    """Cumule le nombre d'appels et de tokens dans `stats` (si fourni)."""
    if stats is None:
        return
    stats["calls"] = stats.get("calls", 0) + 1
    usage = getattr(response, "usage", None)
    if usage is not None:
        stats["tokens"] = stats.get("tokens", 0) + (getattr(usage, "total_tokens", 0) or 0)


# Deux façons d'imposer l'outil : "required" (standard OpenAI) ou l'outil désigné par son nom,
# selon ce que le serveur llm.lab accepte.
FORCED_TOOL_CHOICES = ("required", {"type": "function", "function": {"name": "web_search"}})


_forced_choice = None  # la forme acceptée par le serveur, retenue après le premier succès


def _first_call_forced(client, model, messages):
    """Premier appel avec recherche imposée ; essaie les deux formes de tool_choice."""
    global _forced_choice
    errors = []
    choices = [_forced_choice] if _forced_choice is not None else FORCED_TOOL_CHOICES
    for choice in choices:
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice=choice
            )
            _forced_choice = choice
            return response
        except BadRequestError as e:
            errors.append(f"tool_choice={choice!r} : {e}")
    raise RuntimeError(
        "le serveur refuse d'imposer la recherche web (" + " ; ".join(errors)
        + "). Relancer avec --no-force-search."
    )


def run_agent(
    messages: list[dict],
    provider: str | None = None,
    stats: dict | None = None,
    verbose: bool = True,
    websearch: bool = True,
    force_search: bool = False,
) -> tuple[str, list[dict]]:
    """Envoie une conversation au modèle, qui peut utiliser web_search.

    Renvoie un tuple (réponse, recherches) :
      - réponse    : le texte de la réponse finale du modèle ;
      - recherches : la liste des recherches web lancées, chacune sous la forme
                     du dictionnaire "détails" de web_search() (requête, moteurs,
                     résultats...). Liste vide si le modèle a répondu sans chercher.

    `messages` est l'historique au format OpenAI, par exemple
    [{"role": "user", "content": "..."}]. La liste est modifiée en place : les
    demandes d'outil et leurs résultats y sont ajoutés au fil des tours.

    `provider` : fournisseur de recherche ("searxng" ou "serper") ; sinon la
    variable SEARCH_PROVIDER du .env, sinon "searxng".

    `stats` (optionnel) : dictionnaire que la fonction remplit avec "calls" (nombre
    d'appels au modèle) et "tokens" (total de tokens consommés).
    `verbose` : False supprime l'affichage "[tool] web_search(...)" (utile en parallèle).
    `websearch` : False appelle le modèle sans l'outil web_search (aucune recherche possible).
    `force_search` : True impose au moins une recherche (tool_choice="required" au premier
    appel ; si l'API le refuse, l'outil est désigné par son nom). Sans effet si websearch=False.
    """
    client = get_client()
    model = os.environ["LLM_LAB_MODEL"]
    if not websearch:
        response = client.chat.completions.create(model=model, messages=messages)
        _record_usage(stats, response)
        return response.choices[0].message.content or "", []

    provider = resolve_provider(provider)
    check_provider(provider)  # échoue tout de suite si la clé ou le nom est invalide
    searches: list[dict] = []  # le détail des recherches effectivement lancées

    for iteration in range(MAX_ITERATIONS):
        # On envoie la conversation + la description des outils disponibles.
        if force_search and iteration == 0:
            response = _first_call_forced(client, model, messages)
        else:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS
            )
        _record_usage(stats, response)
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
            if verbose:
                print(f"[tool] web_search({args.get('query')!r})", file=sys.stderr)
            text, details = web_search(args.get("query", ""), provider)
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
    _record_usage(stats, response)
    return response.choices[0].message.content or "", searches
