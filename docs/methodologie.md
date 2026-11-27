# Méthodologie : codage des lieux d'achat

Ce document décrit les données disponibles, le fonctionnement du pipeline, les hypothèses retenues
et les points les plus techniques. Pour **lancer** les commandes, voir le [README](../README.md).

## 1. Objectif

Dans l'enquête **Budget de famille**, les ménages notent où ils ont fait chaque dépense. Le but est
de ranger chacun de ces lieux d'achat (un libellé et une commune) dans la **nomenclature des types de
magasins** de la Banque de France (BdF).

La nomenclature compte 76 postes sur trois niveaux emboîtés : `1` → `11` → `111`. On y ajoute le code
**`00` (« hors champ »)**, propre au projet, pour les libellés qui ne désignent pas un lieu d'achat :
`A DOMICILE`, `ILLISIBLE`, `VIREMENT`, `PAYPAL`…

Un libellé n'est pas toujours le nom d'une enseigne. Ce peut être une description libre
(« boulangerie du coin »), un club, une association, un service, un organisme ou une personne. Une
bonne partie des difficultés vient de là (voir § 4.6).

## 2. Données à disposition

Les données ne sont pas versionnées. Elles sont sur le S3 du SSP Cloud, dans
`s3://projet-budget-famille/data/shop-types-classification/`. Les programmes les lisent directement
avec DuckDB (variables `AWS_*` du SSP Cloud). On peut aussi les copier dans `data/` avec
`aws s3 cp … --endpoint-url $AWS_ENDPOINT_URL`.

| Fichier | Origine | Contenu | Format |
|---|---|---|---|
| `liste_magasins_a_coder.csv` | enquête | Couples libellé × commune à coder, **non annotés**. 13 243 lignes, 10 773 libellés distincts, commune absente pour 1 817 lignes. | TSV, latin-1, CRLF. **En-tête décalé d'une colonne** : la 1re colonne (sans nom) est un identifiant, `MAG_DEP` est en fait la 2e colonne (libellé) et `COM_DEP` la 3e (commune). |
| `liste_magasins_a_coder.parquet` | dérivé du CSV | Mêmes données, colonnes remises en place : `ID` (entier), `MAG_DEP`, `COM_DEP` (`null` si absente). C'est l'entrée normale du pipeline. | Parquet, UTF-8 |
| `Liste_Pointsdevente_BDF2026.csv` | DRTI | **Liste annotée** de 1 111 enseignes. Elle sert à écrire les règles regex, à les contrôler et à fournir des exemples au LLM. | CSV `;`, UTF-8. Colonnes `identifiant;libelle` ; l'identifiant vaut `<code>_<n°>` (`111_01;Auchan`), le code est le préfixe. |
| `nmcl_magasins.csv` | BdF | Nomenclature brute, avec très peu de documentation : un code et un libellé par poste. | CSV `;`, Windows-1252, colonnes `code;libellé` |
| `nmcl_magasins_exemples.csv` | dérivé (`llm.nomenclature_exemples`) | Nomenclature **enrichie** : au plus 10 exemples annotés par code (373 exemples sur 51 codes). C'est celle que le LLM utilise par défaut. | CSV `;`, UTF-8, `code;libellé;exemples`, exemples séparés par ` \| ` |
| `samples/` | `sampling.draw_sample` | Échantillons tirés de la liste à coder : `liste_magasins_a_coder_n<taille>_seed<graine>_<JJMMAAAA>.parquet` | Parquet |
| `runs/run_JJMMAAAA_HHMM_SS/` | `run.py` | Une sortie par exécution : `<nom>_coded.parquet` | Parquet |

En local, `data/sorties/evaluation_n300_run_26112026_1626_29.csv` contient la relecture manuelle de
300 lignes, avec un verdict et le code attendu. Il sert de **jeu d'or** pour comparer les runs
(voir § 6).

Pièges connus dans les données :
- Les libellés sont en majuscules, presque sans accents, et certains sont mal encodés
  (`THEA¢TRE` pour THÉÂTRE, `CRAªPERIE` pour CRÊPERIE).
- Les codes `420` et `990` de la liste annotée n'existent pas dans la nomenclature : aucune règle ne
  les produit, et ils ne servent pas d'exemples.
- La commune est souvent absente ou inexacte : un simple numéro de département, une ville voisine…

## 3. Le pipeline

Le schéma complet est dans [`processus.excalidraw`](processus.excalidraw). Il est généré par
`gen_processus.py`.

```
entrée (CSV ou Parquet : libellé, commune)
  │
  ▼
1. regex (rules.coder) ──► code / hors_champ ──────────────────────────────┐
  │ ambigu / aucune                                                       │
  ▼                                                                       │
2. dédoublonnage par couple (libellé, commune), lecture du cache          │
  │                                                                       │
  ▼                                                                       │
3. LLM llm.lab (prompt + nomenclature enrichie)  ⇄  web_search            │
  │        (SearXNG ou Serper)                                            │
  ▼                                                                       │
4. lecture et validation du JSON {code, confiance, justification}         │
  │ → écrit dans le cache                                                 │
  ▼                                                                       ▼
5. contrôle de cohérence entre libellés équivalents ─► 6. écriture (CSV, ou Parquet sur le S3)
```

1. **Regex** (`rules/rules.py`, fonction `coder`). Chaque libellé, une fois normalisé (majuscules,
   sans accents, ponctuation remplacée par des espaces), est confronté aux 267 règles. Il en sort un
   statut :
   - `code` ou `hors_champ` : le code est posé par la regex, sans appel au LLM ;
   - `ambigu` (des règles se contredisent) ou `aucune` (aucune règle ne matche) : le libellé passe
     au LLM.
2. **Dédoublonnage.** Le LLM n'est appelé qu'une fois par couple (libellé, commune) distinct.
   `--limit` plafonne le nombre d'appels ; les lignes non traitées restent en `pending`.
3. **LLM et recherche web** (`llm/classification.py`, `llm/websearch.py`). Le prompt contient le
   contexte de l'enquête, la nomenclature avec ses exemples annotés, le libellé, la commune, les
   consignes de recherche, les pièges d'interprétation et les règles de choix. Le modèle peut appeler
   l'outil `web_search` jusqu'à 5 fois avant de répondre (§ 5.1).
4. **Validation.** La réponse doit contenir un JSON dont le code existe dans la nomenclature. Sinon,
   la ligne passe en `method=error`. Chaque réponse valide est ajoutée au cache.
5. **Cohérence** (`common/coherence.py`). Les libellés équivalents qui ont reçu des codes différents
   sont signalés (§ 4.8).
6. **Écriture.** Le fichier de sortie reprend les colonnes d'entrée et y ajoute les colonnes
   suivantes.

| Colonne | Contenu |
|---|---|
| `code`, `store_type` | Code final et son libellé dans la nomenclature (`00` = hors champ) |
| `method` | Origine du code : `regex`, `llm`, `error` (échec du LLM) ou `pending` (pas traité) |
| `regex_status` | `code`, `hors_champ`, `ambigu` ou `aucune` |
| `regex_rules`, `regex_tier` | Règle(s) gagnante(s) (ex. `412-03A`) et leur tier |
| `regex_candidate_codes` | Codes en conflit pour un libellé ambigu (ex. `411,413`) |
| `llm_model`, `llm_search_provider` | Modèle utilisé ; `searxng`, `serper`, ou `none` avec `--no-websearch` |
| `llm_websearch`, `llm_search_count`, `llm_queries` | Le modèle a-t-il cherché, combien de fois, avec quelles requêtes |
| `llm_justification`, `llm_confidence` | Justification du modèle, et sa confiance : `haute`, `moyenne` ou `basse` |
| `llm_tokens` | Tokens consommés |
| `error` | Message d'erreur si le LLM a échoué |
| `coherence_codes` | Codes divergents du groupe de libellés équivalents (ex. `129,323`), vide si cohérent |
| `trace_json` | Avec `--trace` seulement : détail complet des recherches (moteurs, résultats, URL) |

Dans un Parquet, `llm_websearch` est un booléen, `llm_search_count` et `llm_tokens` sont des
entiers, et les cases vides valent `null`.

## 4. Hypothèses et choix

### 4.1 Les regex d'abord, le LLM pour le reste

Les regex sont gratuites, déterministes et faciles à relire. Sur l'échantillon n300, elles codent
37 % des lignes avec 94 % de codes justes et moins de 3 % de faux. Le LLM, lui, se trompe environ
4 fois plus souvent. On ne confie donc au LLM que ce que les regex ne savent pas coder sans risque.

### 4.2 Comment sont écrites les règles

- **Source.** Les règles sont déduites de la liste annotée. Une contrainte est vérifiée par
  `rules.evaluate_rules` : un libellé annoté ne doit **jamais** recevoir un autre code que le sien.
  Il est accepté, en revanche, qu'il ne soit pas codé.
- **Niveaux.** Quand plusieurs règles matchent, la règle du niveau le plus bas l'emporte :
  - 0 : composés prioritaires (60 règles), par ex. `STATION LAVAGE` → 641 bien que le libellé
    contienne `STATION` ;
  - 1 : marques et mots spécifiques (196 règles) ;
  - 2 : mots génériques faibles comme `RESTAURANT`, `BAR` ou `HOTEL` (11 règles).
- **Pas d'extrapolation.** Si plusieurs codes différents matchent au niveau gagnant, le libellé est
  `ambigu` et part au LLM : `SUPER U LIDL`, par exemple. Les marques qui existent sous plusieurs formats
  (Carrefour, Leclerc, Intermarché, Casino) n'ont de règle que pour les formes que la liste annotée
  distingue. Le mot « marché » n'est jamais une règle à lui seul.
- **Tiers.**
  - A (151 règles) : déduites directement de la liste annotée.
  - B (116 règles) : extensions par analogie, à partir de connaissances extérieures (`VITO`, `ENGEN`,
    `DR <nom>`, `BAR <nom>`…). Elles sont à relire, et on peut les désactiver avec `--tiers A`.
- **Hors champ.** Le code `00` n'est posé que par des règles exactes, qui portent sur le libellé
  entier. `A DOMICILE` est hors champ, mais `COIFFEUR A DOMICILE` ne l'est pas.
- **Commerce plutôt que service.** Un mot comme `BOUTIQUE`, `COMPTOIR`, `MAGASIN`, `FOURNITURES` ou
  `PRODUITS` (constante `MOTS_COMMERCE`) empêche la règle des soins personnels (623) de s'appliquer.
  « LA BOUTIQUE DU COIFFEUR » vend des produits capillaires, ce n'est pas un salon : le libellé est
  laissé au LLM plutôt que codé par une règle écrite pour ce cas précis.

### 4.3 Les conventions de codage passent par des exemples

Sur le n300, 19 des 21 erreurs du LLM ne venaient pas d'une enseigne mal identifiée, mais d'une
**convention de codage inconnue**. Par exemple, une animalerie ou un fleuriste vont en 232, un tabac en
239, un parking en 659. Le libellé du poste ne permet pas de le deviner.

La nomenclature enrichie joint donc à chaque code jusqu'à 10 libellés réels de la liste annotée, et le
prompt demande de suivre ces exemples. C'est la correction qui a le plus d'effet, et elle ne coûte
aucune recherche. `--nomenclature …/nmcl_magasins.csv` permet de revenir à la nomenclature sans
exemples, pour comparer.

### 4.4 La recherche web est obligatoire par défaut

Quand le modèle choisit de ne pas chercher, il se trompe deux fois plus souvent : 20 % de faux, contre
10 % avec recherche (n300). Un modèle de cette taille surestime ce qu'il sait, et invente une activité
plausible à partir de la sonorité du nom : PINDER classé en fast-food alors que c'est un cirque.

D'où `--force-search`, activé par défaut : le premier appel impose l'outil (§ 5.1) et le prompt
demande de toujours chercher. Le coût est d'au moins une recherche par libellé envoyé au LLM, soit
environ 13 % de recherches en plus sur le n300. `--no-force-search` laisse la décision au modèle.

### 4.5 S'abstenir plutôt que deviner

Le prompt demande de répondre `99` quand l'enseigne n'est identifiée que de façon probable : plusieurs
homonymes, résultats de recherche qui ne correspondent pas, nom trop générique. Il autorise aussi le
code du regroupement parent quand le modèle hésite entre deux sous-catégories. Un `99` honnête se
repère et se relit ; un code faux passe inaperçu.

Le modèle indique aussi sa **confiance** (`llm_confidence`) :
- `haute` : activité écrite dans le libellé, ou enseigne identifiée par une source trouvée en
  recherche ;
- `moyenne` : activité sûre, mais hésitation entre deux codes voisins ;
- `basse` : activité incertaine, avec un code `99` ou un code de regroupement.

Une enseigne que le modèle « connaît » sans l'avoir vérifiée ne peut pas être en confiance `haute`. Pour
la relecture humaine, commencer par `llm_confidence = basse` et `code = 99`. C'est une confiance
**déclarée par le modèle**, pas une probabilité calibrée. Une valeur absente ou invalide laisse la
colonne vide sans faire échouer la ligne.

### 4.6 Les pièges d'interprétation

Le prompt rappelle que le libellé décrit une dépense, pas forcément un magasin, et cite deux pièges :
- « Boutique du / Comptoir du + métier » désigne un magasin de produits pour ce métier, pas le
  service ;
- `CLUB`, `ASSOCIATION`, `AMICALE`… désignent une adhésion : 622 si c'est un club sportif, 659 sinon.
  Ce n'est pas un magasin qui vend le matériel de l'activité.

La justification doit préciser la lecture retenue (magasin, service, club…), pour faciliter la
relecture.

### 4.7 Une réponse par couple (libellé, commune)

La commune aide à lever les homonymies et à cibler la recherche. Le même libellé peut donc recevoir
deux codes dans deux communes, ce qui est voulu. Le prompt demande au modèle de ne se fier à la
commune que si elle est cohérente.

### 4.8 Contrôle de cohérence

Chaque appel au LLM est indépendant : « LES VERGERS DE VENDEE » et « VERGER DE VENDEE » ont reçu deux
codes différents dans le n300.

En fin de run, les libellés sont regroupés selon une clé normalisée : sans forme juridique (SAS, SARL,
SNC…), sans article initial et au singulier. Si un groupe a reçu plusieurs codes, chacune de ses lignes
porte la liste de ces codes dans `coherence_codes`.

La commune n'entre volontairement pas dans la clé. Deux établissements homonymes de villes différentes
peuvent donc être signalés à tort. C'est un signal de relecture, pas une correction automatique.

## 5. Points techniques

### 5.1 Recherche web sur llm.lab : le function calling

llm.lab sert les modèles par une API compatible OpenAI, mais ne fournit pas de recherche web. On décrit
donc au modèle un outil `web_search` (constante `TOOLS` dans `llm/websearch.py`), et `run_agent()` gère
la boucle d'appels :

1. On appelle le modèle avec les outils disponibles.
2. S'il demande `web_search`, le programme lance la recherche et ajoute les résultats à la
   conversation, sous un message de rôle `tool`. Puis on rappelle le modèle.
3. Dès qu'il répond sans demander d'outil, cette réponse est la réponse finale.
4. Au bout de `MAX_ITERATIONS = 5` tours, un dernier appel avec `tool_choice="none"` l'oblige à
   répondre avec ce qu'il a déjà trouvé.

**Recherche imposée.** Le premier appel utilise `tool_choice="required"`. Si le serveur renvoie une
erreur 400, on désigne l'outil par son nom (`{"type": "function", "function": {"name": "web_search"}}`).
La forme acceptée est mémorisée (`_forced_choice`) pour les appels suivants. Si les deux formes sont
refusées, la ligne passe en `error` avec un message qui suggère `--no-force-search`.

Le prompt s'adapte au mode : la variable `$consignes_recherche` reçoit soit « fais toujours au moins
une recherche », soit « cherche si tu ne connais pas l'enseigne ».

**Ce que voit le modèle, ce qui est tracé.** `web_search()` renvoie deux choses :
- un texte compact (titre, URL, extrait), envoyé au modèle ;
- un dictionnaire détaillé (moteurs qui ont répondu, moteurs en échec avec la raison, résultats), qui
  est conservé pour la trace (`trace_json`) mais que le modèle ne voit jamais.

Une erreur de recherche (service arrêté, clé invalide) est renvoyée au modèle sous forme de texte, au
lieu de faire échouer le programme.

Exemple de trace, telle que l'affiche `llm.classify` dans le champ `recherche_web` (le contenu de
`trace_json` a la même forme) :

```json
{"fournisseur": "searxng", "lancee": true, "recherches": [{
  "requete": "ELECTRO DEPOT Metz",
  "moteurs_utilises": ["brave", "google cse"],
  "moteurs_en_echec": [{"moteur": "duckduckgo", "raison": "CAPTCHA"}],
  "resultats": [{"titre": "…", "url": "https://…", "moteurs": ["brave", "google cse"]}]}]}
```

Avec Serper, `moteurs_utilises` vaut toujours `["google"]` et chaque recherche porte un champ
`credits`.

La boucle est réutilisable depuis un autre script :
`from llm.websearch import run_agent`, puis
`reponse, recherches = run_agent([{"role": "user", "content": "…"}], provider="serper", force_search=True)`.

La requête attendue est le nom exact de l'enseigne suivi de la ville, sans mot ajouté. Des mots comme
« activité » ou « magasin » orientent le moteur vers des résultats génériques, surtout quand le nom est
un mot courant.

### 5.2 Les fournisseurs de recherche

| | SearXNG | Serper |
|---|---|---|
| Nature | méta-moteur installé en local (`common/setup_searxng.sh`, sans Docker) | API payante ([serper.dev](https://serper.dev)) |
| Moteurs | plusieurs (Brave, Google CSE, DuckDuckGo…) ; leur liste varie d'une requête à l'autre | Google uniquement |
| Coût | gratuit | 1 crédit par recherche |
| Fiabilité | moteurs parfois bloqués (captcha, limite de requêtes) | stable |

Chaque fournisseur est une fonction `search_xxx(query, max_results)` de `common/search_providers.py`.
Toutes renvoient le même format (`resultats`, `moteurs_utilises`, `moteurs_en_echec`, et `credits`
pour Serper). Pour ajouter un fournisseur, il suffit d'écrire la fonction et de l'enregistrer dans
`PROVIDERS`.

Le fournisseur est choisi dans cet ordre : option `--search-provider`, puis variable `SEARCH_PROVIDER`,
puis `searxng`. `check_provider()` vérifie ses prérequis avant tout appel au modèle.

### 5.3 Le prompt et la lecture de la réponse

- Le prompt est un fichier texte, `llm/prompts/classification.txt`. On peut changer le comportement du
  modèle sans toucher au code.
- C'est un `string.Template` : les variables s'écrivent `$nomenclature`, `$libelle`, `$ville` et
  `$consignes_recherche`, et non `{…}`, parce que le prompt contient lui-même des accolades JSON.
- `parse_answer()` prend le texte compris entre la première `{` et la dernière `}`. Cela tolère un
  bloc ```` ```json ```` ou du texte autour du JSON. Elle vérifie ensuite que le code existe dans la
  nomenclature.
- Le libellé du poste (`store_type`) est **recopié depuis la nomenclature**, jamais depuis la réponse
  du modèle.
- `classify_with_llm()` ne lève jamais d'exception : erreur, réponse brute, recherches et tokens sont
  dans le dictionnaire qu'elle renvoie. Une ligne en échec ne fait donc pas tomber le run.

### 5.4 Cache et reprise

Chaque réponse valide est ajoutée immédiatement à un fichier JSONL, sous la clé (libellé, commune) :
- pour un CSV : `<sortie>.cache.jsonl` ;
- pour un Parquet : `data/cache/<nom>_coded.cache.jsonl`. Le cache est lié au fichier d'entrée, car
  chaque run a son propre dossier de sortie.

Relancer la commande reprend là où elle s'était arrêtée. Les échecs ne sont pas mis en cache : ils
sont retentés. Un Ctrl+C garde ce qui est déjà obtenu.

La clé du cache ne contient **ni le modèle ni le prompt**. Après un changement de prompt, de modèle,
de nomenclature ou de mode de recherche, il faut donc utiliser `--no-cache`. C'est l'option normale en
phase d'évaluation.

### 5.5 Parquet et S3, avec DuckDB seulement

Il n'y a ni pandas ni pyarrow : tout passe par DuckDB. `common/s3.py` crée le secret S3 à partir des
variables `AWS_*`.

Pour réutiliser le pipeline CSV, une entrée Parquet est convertie en texte. Les colonnes ajoutées sont
écrites dans un JSONL temporaire typé, puis **rejointes au Parquet d'origine sur `file_row_number`**.
Les colonnes d'entrée gardent ainsi exactement leur type, et l'ordre des lignes est conservé.

Toute exécution, même `--dry-run`, lit la nomenclature sur le S3. Hors SSP Cloud, il faut passer un
fichier local avec `--nomenclature`.

### 5.6 Parallélisme et suivi

Les appels au LLM partent en parallèle dans un pool de threads (`--workers`, 4 par défaut). Une ligne
s'affiche à chaque réponse, avec le code, le nombre de recherches et la durée. Une ligne de suivi
s'affiche aussi au moins toutes les 30 s, avec une estimation de la fin.

Le client OpenAI a un délai maximal `LLM_LAB_TIMEOUT` (120 s par défaut) et une seule nouvelle
tentative. Un appel bloqué finit donc en `error` au lieu de bloquer le run.

### 5.7 Échantillonnage reproductible

`sampling.draw_sample` trie les lignes par `md5(graine || '-' || ID)` et garde les premières :
- même graine, même échantillon, sur n'importe quelle machine ;
- pour une même graine, l'échantillon de 100 est inclus dans celui de 200.

### 5.8 Choix des exemples annotés

`llm.nomenclature_exemples` choisit les exemples de façon déterministe :
1. pour chaque code, les libellés annotés sont regroupés par leurs 5 premiers caractères normalisés
   (Boucher, Boucherie et Boucherie halal forment un seul groupe) ;
2. les groupes les plus fournis passent en premier, car ce sont souvent les types génériques ;
3. on garde le libellé le plus court de chaque groupe ;
4. on écarte les variantes trop proches d'un exemple déjà retenu : préfixe commun, ou similarité
   ≥ 0,8 (`H M` / `H&M`).

### 5.9 Schéma du processus

`docs/processus.excalidraw` est généré par `docs/gen_processus.py`. Après tout changement du pipeline,
on modifie les textes dans ce script et on le relance. Il vérifie lui-même que les blocs ne se
chevauchent pas et qu'aucune flèche ne traverse un texte. On ne modifie pas le fichier à la main dans
Excalidraw.

## 6. Évaluation et limites

**Évaluation du run `run_26112026_1626_29`** (n300, qwen3-8-27b, Serper, avant l'ajout des exemples
annotés et de la recherche obligatoire). Détail dans le
[rapport d'évaluation](evaluation_run_26112026_1626_29.md).

| Méthode | Lignes | % juste | % faux |
|---|---:|---:|---:|
| Regex (code et hors champ) | 112 | 93,8 % | 2,7 % |
| LLM avec recherche web | 163 | 83,4 % | 9,8 % |
| LLM sans recherche | 25 | 76,0 % | 20,0 % |
| **Total** | **300** | **86,7 %** | **8,0 %** |

Ce run a motivé plusieurs choix décrits plus haut :
- les exemples annotés (§ 4.3) ;
- la recherche obligatoire (§ 4.4) ;
- l'abstention et la confiance (§ 4.5) ;
- le contrôle de cohérence (§ 4.8).

Pour mesurer l'effet de ces changements, relancer avec `--no-cache` et comparer au jeu d'or
`data/sorties/evaluation_n300_run_26112026_1626_29.csv`, de préférence sur un autre échantillon. Les
cas cités en exemple dans le prompt ne prouvent rien sur la capacité du modèle à généraliser.

**Limites connues**
- SearXNG s'arrête quand le service SSP Cloud redémarre. Certains de ses moteurs renvoient des
  captchas selon l'IP ; la recherche fonctionne alors avec les moteurs restants.
- Serper est payant : au moins une recherche par libellé envoyé au LLM en mode par défaut.
- Chaque appel au LLM est indépendant : le contrôle de cohérence signale les incohérences mais ne
  les corrige pas.
- Le contrôle de cohérence ignore la commune, et la confiance est déclarée par le modèle lui-même
  (§ 4.5, § 4.8).
- Certaines frontières de la nomenclature restent floues malgré les exemples : 313/314 (boutique en
  ligne d'une enseigne ou site seulement en ligne), 239/659, 114/322.
