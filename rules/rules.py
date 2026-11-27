"""
rules.py : règles déterministes (regex) de codage d'une enseigne dans la nomenclature.

PRINCIPE
    Les règles sont déduites de la liste annotée Liste_Pointsdevente_BDF2026.csv
    (identifiant "111_01" = code de nmcl_magasins.csv + "_" + n°, libellé). Un libellé
    est d'abord normalisé (normaliser), puis confronté à toutes les règles. Ce qui n'est
    pas codé par une règle (statut "aucune" ou "ambigu") doit passer par le workflow
    LLM + recherche web (classify.py).

    Les codes 420 et 990 de la liste annotée n'existent pas dans nmcl_magasins.csv : aucune
    règle ne les produit.

NIVEAUX (le niveau le plus bas qui matche l'emporte)
    0 : composés prioritaires (ex. "STATION LAVAGE" -> 641 même s'il contient "STATION",
        "RESTAURANT SCOLAIRE" -> 421 même s'il contient "RESTAURANT", "DRIVE" -> 312).
    1 : marques et mots spécifiques.
    2 : mots génériques faibles ("RESTAURANT", "BAR", "TABAC"...) : ils ne comptent que
        si aucune règle de niveau 0 ou 1 ne matche.

ANTI-EXTRAPOLATION
    Si, au niveau gagnant, les règles donnent des codes différents, le libellé est
    "ambigu" et n'est PAS codé (ex. "SUPER U LIDL"). Une marque qui existe en plusieurs
    formats dans la nomenclature (Carrefour, Leclerc, Intermarché, Casino, Auchan, U...)
    n'a pas de règle sur le mot seul, seulement sur les formes que la liste annotée
    distingue. Le mot "marché" n'est jamais une règle en soi.

TIERS
    A : déduit directement de la liste annotée (le libellé, ses variantes orthographiques,
        la marque comme mot).
    B : extension par analogie, fondée sur des connaissances extérieures à la liste
        annotée (ex. VITO, ENGEN, DOUKA...). À relire ; désactivable (tiers=("A",)).

UTILISATION
    from rules import coder
    coder("MC DONALD'S LYON")  # {"code": "412", "statut": "code", "regles": [...], ...}
"""

import re
import unicodedata
from dataclasses import dataclass

NIVEAU_FORT, NIVEAU_MOT, NIVEAU_FAIBLE = 0, 1, 2


def normaliser(libelle: str) -> str:
    """Majuscules, sans accents, "&" -> " ET ", tout le reste non alphanumérique -> espace."""
    s = unicodedata.normalize("NFKD", libelle.upper())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", s.replace("&", " ET ")).strip()


@dataclass(frozen=True)
class Regle:
    id: str
    code: str
    motif: re.Pattern
    niveau: int
    tier: str
    ref: str  # libellé(s) de la liste annotée qui justifient la règle (ou "ext." pour tier B)


REGLES: list[Regle] = []
_compteur: dict[str, int] = {}


def W(*alts: str) -> str:
    """Un des mots/expressions, délimité par des frontières de mots."""
    return r"\b(?:" + "|".join(alts) + r")\b"


def X(*alts: str) -> str:
    """Libellé entier égal à l'une des expressions."""
    return r"^(?:" + "|".join(alts) + r")$"


def add(code: str, motif: str, niveau: int = NIVEAU_MOT, tier: str = "A", ref: str = ""):
    _compteur[code] = _compteur.get(code, 0) + 1
    REGLES.append(
        Regle(f"{code}-{_compteur[code]:02d}{tier}", code, re.compile(motif), niveau, tier, ref)
    )


# --------------------------------------------------------------------------------------
# 0 : composés prioritaires (annulent le mot générique ou la marque qu'ils contiennent)
# --------------------------------------------------------------------------------------
# Nouveaux fournisseurs de repas / internet de marques physiques
add("313", W("FNAC COM", "SNCF COM", r"VERT ?BAUDET INTERNET"), 0, ref="Fnac.com, Sncf.com, Vert Baudet internet")
add("313", W("BOULANGER COM"), 0, "B", ref="ext. (analogue Fnac.com)")
add("321", W("ALLO RESTO", "LIVRAISON (?:DE )?PIZZAS?", "REPAS A DOMICILE"), 0, ref="Allo Resto, Livraison pizza, Repas à domicile")
add("321", W("UBER EATS?", "DELIVEROO", "JUST EAT"), 0, "B", ref="ext.")

# Drive : prime sur la marque (Super U Drive, Leclerc Drive) sauf restauration rapide (McDrive)
add("312", r"^(?!.*\b(?:MC ?DO|MAC ?DO|MC ?DONALDS?|MAC ?DONALDS?|BURGER KING|KFC|QUICK|FLUNCH|CHICKEN|FAST FOOD|MC ?DRIVE)\b).*\bDRIVE\b",
    0, ref="Drive, Auchan/Carrefour/Leclerc/Intermarché Drive")
add("312", W("AUCHAN DIRECT", "CHRONODRIVE", "HOURA"), 0, ref="Auchan Direct, Chronodrive, Houra")

# Stations-service : prime sur la marque d'enseigne de grande distribution
add("641", W(r"STATION (?:DE )?LAVAGE", r"LAVAGE (?:AUTO|AUTOMATIQUE|VOITURE)(?:S)?", "LAVE AUTO"), 0,
    ref="Station de lavage, Station lavage auto, Lavage Auto, Lavage automatique")
# Station-service + nom d'enseigne de supermarché (dans n'importe quel ordre) -> station-service.
# La station vendant du carburant, l'enseigne de la grande distribution ne change pas son type.
MARQUES_GD = (r"E ?LECLERC|LECLERC|CARREFOUR(?: MARKET| CITY| CONTACT| EXPRESS)?|AUCHAN|INTERMARCHE|(?:SUPER |HYPER |SYSTEME )?U|CASINO|CORA|GEANT|"
              r"LIDL|ALDI|MONOPRIX|NETTO|LEADER PRICE|MATCH|SIMPLY(?: MARKET)?|FRANPRIX|COLRUYT|SUPERMARCHE|HYPERMARCHE|MILENIS|SUPECO|SPAR|VIVAL|PROXI|ATAC|"
              r"ECOMARCHE|SHOPI|KAUFLAND|NORMA|SCORE|JUMBO")
MOTS_STATION = r"STATION|CARBURANTS?|POMPE|ESSENCE|PETRO|GAZOLE|GASOIL"
# Mots qui désignent un commerce : avec un mot de service (« LA BOUTIQUE DU COIFFEUR »), le
# libellé peut être un magasin de produits pour ce métier, pas le service lui-même -> au LLM.
MOTS_COMMERCE = r"BOUTIQUES?|COMPTOIRS?|MAGASINS?|FOURNITURES?|PRODUITS?|GROSSISTES?|MATERIELS?"
add("235", r"^(?!.*\b(?:LAVAGE|SKI)\b)(?=.*\b(?:" + MOTS_STATION + r")\b)(?=.*\b(?:" + MARQUES_GD + r")\b)", 0,
    ref="Station Leclerc, Station Carrefour, Station Intermarché, Station supermarché, Leclerc Station (+ toute enseigne de supermarché, dans les deux ordres)")
add("235", W(r"STATION (?:SERVICE|ESSENCE|CARBURANTS?|D ESSENCE|(?:DE |D )?AUTOROUTES?|BP|ESSO|SHELL|AVIA|TOTAL ?(?:ENERGIES?)?|ENGEN|VITO)",
             r"POMPE (?:A ESSENCE|HYPER U)", "SATION SERVICE", "RELAIS TOTAL"),
    0, ref="Station service/essence/carburant/d'autoroute/Total/Shell/Esso/Bp")
add("235", W(r"PETRO (?:[A-Z]+ )*E LECLERC", "PETRO"), 0, "B", ref="ext. (Petro = carburant)")

# Bars : "bar tabac", "bar presse"... restent des bars (413) et non des tabacs (239) / presse (231)
add("411", X("RESTAURANT BAR"), 0, ref="Restaurant bar")
add("413", r"\b(?:BAR|CAFE|BISTROT?)\b.*\b(?:TABAC|PRESSE|PMU|RESTAURANT|RESTO|BRASSERIE)\b|"
           r"\b(?:TABAC|PRESSE)\b.*\b(?:BAR|CAFE)\b", 0,
    ref="Bar tabac, Bar presse, Bar tabac presse, Café tabac, Bar Pmu, Bar restaurant, Bar Resto, Bar brasserie")
# Tabac + presse / journaux
add("231", r"^(?!.*\b(?:BAR|CAFE)\b)(?:.*\bTABAC\b.*\b(?:PRESSE|JOURNAUX)\b|.*\b(?:PRESSE|JOURNAUX)\b.*\bTABAC\b)", 0, ref="Presse tabac, Tabac presse, Tabac journaux")
add("231", W("BUREAU DE LOTO"), 0, ref="Bureau de loto")

# Restauration collective (prime sur "restaurant", "cantine", "cafétéria")
add("421", W("RESTAURATION SCOLAIRE", "RESTAURANT SCOLAIRE", "RESTO SCOLAIRE", "RESTO CROUS", "RESTO U", "RESTO UNIVERSITAIRE", "RESTAURANT UNIVERSITAIRE"), 0,
    ref="Restauration scolaire, Restaurant Scolaire, Resto Crous/U/Universitaire/scolaire")
add("421", X("RU"), 0, ref="Ru")
add("422", W(r"CAFETERIA (?:D )?(?:ENTREPRISE|TRAVAIL)", r"CANTINE (?:SCOLAIRE|TRAVAIL|ENTREPRISE|D ENTREPRISE)",
             r"RESTAURANT (?:D )?(?:ENTREPRISE|TRAVAIL)", r"RESTO (?:D )?ENTREPRISE"), 0,
    ref="Cafétéria entreprise/travail, Cantine scolaire/travail/Entreprise, Restaurant d'entreprise/travail, Resto entreprise")
add("659", W(r"RESTOS? DU COEUR"), 0, ref="Resto du Coeur")
add("411", X("LA BOUCHERIE"), 0, ref="La Boucherie (restaurant dans la liste annotée)")
add("412", W(r"(?:LE )?KIOSQUES? A PIZZAS?", "PIZZERIA A EMPORTER", "RESTAURANT RAPIDE", "RESTAURATION RAPIDE", r"SNACK(?: ?[ -])?BAR"), 0, ref="Le Kiosque à Pizzas, Pizzeria à emporter, Restaurant rapide, Snack Bar")
add("422", W(r"CANTINE (?:HOPITAL|CHU|CLINIQUE)"), 0, "B", ref="ext.")
add("659", W("CLINIQUE VETO"), 0, "B", ref="ext. (analogie Clinique vétérinaire)")
add("114", X("FERME DU SPAHI"), 0, ref="Ferme du Spahi (exception de la liste annotée)")
add("511", W(r"HOTEL (?:BAR )?(?:ET )?RESTAURANT", r"RESTAURANT HOTEL", "HOTEL RESTO"), 0, ref="Hôtel restaurant")

# Vente ambulante : prime sur le produit vendu (Boucherie ambulante, Boulanger ambulant)
add("322", W(r"AMBULANT(?:E|S|ES)?"), 0,
    ref="Boucherie Ambulante, Boulanger ambulant, Marchand ambulant, Marchande ambulante, Marché ambulant, Vendeur ambulant, Vente Ambulante")
add("322", W("BOUCHERIE MARCHE", "CHARCUTIER MARCHE", "FROMAGER MARCHE", r"PRIMEURS? MARCHE",
             r"MARCHE (?:BIO|COUVERT|FORAIN|LOCAL|DE NOEL|PRIMEURS?|AUX PUCES)"), 0,
    ref="Boucherie marché, Charcutier marché, Fromager marché, Primeur marché, Marché bio/Couvert/forain/local/de Noël/primeur(s)/aux puces")
add("212", W(r"FOIRE? ?FOUILLE", "FOIR FOUILLE", "FOIFOUILLE"), 0, ref="Foir Fouille, Foirfouille, La Foifouille, La Foir' Fouille")
add("622", W(r"FETE FOR+AINE"), 0, ref="Fête foraine, Fête forraine")

# Compléments de grande distribution : galeries / enseignes dédiées d'un centre Leclerc
add("222", W(r"BRICO(?:LAGE)? E LECLERC", "BRICO LECLERC", "LECLERC BRICO", r"JARDI(?:N)? E LECLERC", r"E LECLERC JARDIN", "LECLERC JARDIN"), 0,
    ref="Brico Leclerc, Leclerc Brico, Leclerc Jardin")
add("221", W(r"(?:ESPACE )?CULTUREL E LECLERC", "LECLERC CULTUREL"), 0, ref="Leclerc Culturel")
add("239", W(r"(?:L )?AUTO E LECLERC", r"E LECLERC AUTO", "AUTO LECLERC"), 0, ref="Auto Leclerc")
add("114", W(r"CARREFOUR (?:CITY|CONTACT|EXPRESS)", r"INTERMARCHE EXPRESS", r"U ?EXPRESS", "PETIT CASINO", "CASINO SHOP", "SUPERETTE CASINO"), 1,
    ref="Carrefour City/Contact/Express, Intermarché Express, U Express, Petit Casino, Casino Shop, Supérette Casino")
add("112", W("CARREFOUR MARKET", "SIMPLY MARKET", "SYMPLY MARKET", "AUCHAN CITY", "SUPER U", "SYSTEME U", "MARCHE U", "SUPERMARCHE U"), 1,
    ref="Carrefour Market, Simply Market, Symply Market, Auchan City, Super U, Systeme U, Marché U, Supermarché U")
add("111", W("HYPER U", "HUPER U", "JUMBO SCORE", "GEANT CASINO"), 1, ref="Hyper U, Jumbo Score, Géant Casino")
add("235", W("AVIA XPRESS"), 0, "B", ref="ext. (Avia)")

# Compléments : composés de la liste annotée qui contiennent un mot générique
add("129", W(r"EPICERIE (?:FINE|ITALIENNE)", r"CHARCUTIER TRAITEUR", r"BOUCHERIE ?/? ?TRAITEUR"), 0,
    ref="Epicerie fine, Epicerie Italienne, Charcutier traiteur, Boucherie/traiteur")
add("121", W(r"TRAITEUR BOULANGERIE", "BOULANGERIE EPICERIE"), 0, ref="Traiteur Boulangerie, Boulangerie Epicerie")
add("114", W("DEPOT (?:DE )?PAIN"), 0, ref="Dépot de pain, Dépôt pain")
add("232", W("ALIMENTATION ANIMALE"), 0, ref="Alimentation animale")
add("232", W(r"MAGASIN (?:DE )?(?:BRICOLAGE|DECO(?:RATION)?|VAISSELLE|TISSUS?|VERT)"), 0,
    ref="Magasin bricolage/de bricolage/déco/décoration/de vaisselle/Tissu/Vert")
add("231", W(r"MAGASIN (?:DE )?(?:JOUETS?|SPORT(?! 2000)|PHOTO|INFORMATIQUE)"), 0, ref="Magasin de jouets/sport/Photo/Informatique, Magasin Jouet")
add("233", W(r"MAGASIN (?:DE )?(?:BEAUTE|COSMETIQUE|MAQUILLAGE|CHAUSSURES?|PRET A PORTER|VETEMENTS?)"), 0,
    ref="Magasin beauté/cosmétique/maquillage/chaussure(s)/de prêt à porter/de vêtements")
add("329", W("CAVE COOPERATIVE", "COOPERATIVE AGRICOLE"), 0, ref="Cave coopérative de vinification, Coopérative agricole")
add("653", W("BANQUE POSTALE"), 0, ref="Banque Postale")
add("659", W(r"AUTO ?ECOLE", "ASSOCIATION CARITATIVE", "ASSOCIATION ECOLE", "CABINET VETERINAIRE", "CLINIQUE VETERINAIRE", "POLICLINIQUE VETERINAIRE"), 0,
    ref="Auto-école, Association caritative, Association école, Cabinet vétérinaire, Clinique vétérinaire")
add("621", W("ECOLE DE MUSIQUE"), 0, ref="Ecole de musique")
add("622", W("ECOLE DE DANSE", "ASSOCIATION SPORTIVE"), 0, ref="Ecole de danse, Association Sportive")
add("652", W("CENTRE DE LOISIRS", "OFFICE HLM", "BAILLEUR SOCIAL"), 0, ref="Centre de loisirs, Office HLM, Bailleur Social")
add("622", W("BASE DE LOISIRS", "PARC DE LOISIRS"), 0, ref="Base de loisirs, Parc de loisirs")
add("412", W(r"AIRE D AUTOROUTE", "AUTO GRILL"), 0, ref="Aire d'autoroute, Auto Grill")
add("412", W("RESTO RAPIDE", r"FAST ?FOOD", "BURGER KING", "KFC", r"MAC ?DONALD(?: ?S)?", r"MC ?DONALD(?: ?S)?", r"MAC ?DO", r"MC ?DO"), 0,
    ref="Fast food, Burger King, KFC, McDonald's (priment sur 'restaurant' / 'resto')")
add("611", W(r"CENTRE (?:MEDICAL|RADIOLOGIE)", r"CABINET (?:DENTAIRE|INFIRMIER|KINE|MEDECIN|MEDICAL|RADIOLOGIE)"), 0,
    ref="Centre Médical, Cabinet dentaire/médical/Kiné...")

# --------------------------------------------------------------------------------------
# 1 : marques et mots spécifiques, par code
# --------------------------------------------------------------------------------------
# --- 111 Hypermarchés
add("111", X(r"(?:E )?LECLERC", r"(?:HYPER|HYPERMARCHE|GRANDE SURFACE|CENTRE|MAGASIN|CHEZ) (?:E )?LECLERC",
             r"(?:MAGASIN |HYPERMARCHE )?CARREFOUR", r"(?:HYPERMARCHE |HALLES? (?:D )?)?AUCHAN",
             r"(?:LE )?GEANT", "MILENIS", "HYPERMARCHE"), ref="E.Leclerc, Leclerc, Hypermarché Leclerc/Auchan, Carrefour, Auchan, Halles Auchan, Géant, Milenis")
add("112", X("LES HALLES D AUCHAN", "GRANDE SURFACE", "GRANDE DISTRIBUTION", "MAGIK DEPOT"), ref="Les Halles d'Auchan, Grande surface, Grande distribution, Magik'Dépôt")
add("111", W("HYPERMARCHES?"), ref="Hypermarché")
add("111", r"^E LECLERC(?!(?: [A-Z0-9]+)*? (?:PRESSE|CULTURE|CULTUREL|INTERNET|TICKET|DRIVE|AUTO|BRICO\w*|JARDI\w*|STATION|CARBURANTS?|ESSENCE|OPTIQUE|VOYAGES?|PARFUMERIE|PHOTO|FLEURS|LOCATION|CAFETERIA|GALERIE|ESPACE|ANIMALERIE|PETRO|GRAND|ARTS|COIFFEUR|CORDONNIER|TABAC|PHARMACIE|BOULANGERIE|PRESSING|MULTIMEDIA|BIJOUTERIE|MAROQUINERIE|DISTRIBUTEUR)\b)(?: [A-Z0-9]{2,}){1,3}$", NIVEAU_FAIBLE, "B", ref="ext. (E.Leclerc + lieu)")

# --- 112 Supermarchés
add("112", W("SUPERMARCHES?"), ref="Supermarché, Supermarché Casino/Atac/bio/Discount")
add("112", W("BIO C ?BON", "BIOCOOP", "BIOCOP", "NATURALIA", "LA VIE CLAIRE", "VIE CLAIRE", "SATORIZ", "NATUREO", "TY BIO", "LA MAISON VITALE"),
    ref="Bio C Bon, Biocoop, Biocop, Naturalia, La Vie Claire, Satoriz, NaturéO, Ty Bio, La Maison Vitale")
add("112", W("COLRUYT", "KAUFLAND", "MONOPRIX", "TANG FRERES", "PROMOCASH", "GRAND ?FRAIS", "MAXIMARCHE", "EUROMERCAT", "MOUSQUETAIRES",
             "SUPERMARCHE ATAC", "DIET DISCOUNT", "GALERIES GOURMANDES", "NOUVEAUX ROBINSON", "MARCHE FRAIS", "GROUPE METRO", "TESCO", "PARIS STORE"),
    ref="Colruyt, Kaufland, Monoprix, Tang Frères, Promocash, Grand Frais, Maximarche, Euromercat, Mousquetaires, Marché Frais, Paris Store...")
add("112", X(r"(?:L )?EAU VIVE", "MATCH", "SCORE", "SIMPLY", "CORA", "NORMA", "CASINO", "INTERMARCHE", r"A 2 PAS",
             "LA TRAMUNTANA", "POLYGONE", r"MARCHE MARGUERITE"), ref="Eau Vive, Match, Score, Simply, Cora, Norma, Casino, Intermarché (libellés exacts)")
add("112", W("MARCHE FRAIS BY CARREFOUR"), ref="Marché Frais")

# --- 113 Maxi-discompte
add("113", W("ALDI", "LIDL", "LEADER PRICE", "ECOMAX", "DISCOUNT ALIMENTAIRE"), ref="Aldi, Lidl, Leader Price, Ecomax, Discount alimentaire")
add("113", X("DISCOUNT"), ref="Discount")

# --- 114 Supérettes, épiceries
add("114", W(r"8 ?A ?HUIT", "FRANPRIX", r"G ?20", "NETTO", "MONOP", "THIRIET", "TOUPARGEL", "SHOPI", "SPAR", "VIVAL", "VIVECO",
             r"COCCI ?MARKET", "COCCINELLE", "DIAGONAL", r"ECO ?MARKET", "ECOMARCHE", "RAPID MARKET", r"PROXI(?: MARCHE| SUPER)?", "PROXY",
             "PICARD", "CARREFOUR NETTO"), ref="8 à Huit, Franprix, G20, Netto, Monop', Thiriet, Toupargel, Shopi, Spar, Vival, Viveco, Coccinelle, Eco Market, Proxi, Picard...")
add("114", X("DIA", "UTILE", "ATAC", "ALIMENTATION", r"ALIMENTATION GENERALE"), ref="Dia, Utile, Atac, Alimentation, Alimentation générale")
add("114", W("SUPERETTES?", r"EPICERIE", "EPICIER", r"MAGASIN (?:D )?ALIMENT(?:AIRE|ATION)", "COMMERCE ALIMENTAIRE", "COMMERCE EPICERIE",
             r"ALIMENTATION GENERALE", r"MAGASIN (?:DE )?BIO", "MARCHAND BIO", "MAGASIN DIETETIQUE", "BOUTIQUE BIO"),
    ref="Supérette(s), Epicerie (asiatique/bio/chinoise/exotique/générale/sociale/solidaire), Epicier, Magasin alimentaire, Magasin bio, Marchand bio...")
add("114", W("DOUKA"), 1, "B", ref="ext. (petite épicerie à Mayotte)")

# --- 121 Boulangerie, pâtisserie, chocolaterie
add("121", W("BOULANGERIES?", "ARTISAN BOULANGER", "PATISSERIES?", "PATISSIER", r"CHOCOLAT(?:ERIE|IER)", r"CONFISE(?:RIE|UR)",
             "JEFF DE BRUGES", "LEONIDAS", "GRENIER A PAIN"),
    ref="Boulangerie(s), Artisan Boulanger, Pâtisserie, Pâtissier, Chocolaterie, Chocolatier, Confiserie, Confiseur, Jeff De Bruges, Leonidas")
add("121", X("BOULANGER", "PAIN"), ref="Boulanger, Pain (libellés exacts ; 'BOULANGER <lieu>' est ambigu avec l'enseigne d'électroménager)")

# --- Extensions B fréquentes dans la liste à coder (connaissances extérieures, à relire)
add("233", W("CACHE CACHE", "GENERALE D OPTIQUE"), 1, "B", ref="ext. (enseignes d'habillement / d'optique)")
add("121", W("FOURNIL", "MIE DE PAIN", "POMME DE PAIN", "PETISSERIE"), 1, "B", ref="ext. (fournil = boulangerie ; typo pâtisserie)")
add("129", W(r"CAVE A VINS?"), 1, "B", ref="ext. (analogie Caviste)")
add("322", W("VOITURE A PAIN"), 1, "B", ref="ext. (analogie Boulanger ambulant)")
add("659", W("INDIGO", "EASY PARK", "EFFIA", "HORODATEUR"), 1, "B", ref="ext. (analogie Parking)")
add("641", W("DEKRA", "ROADY"), 1, "B", ref="ext. (analogie Contrôle technique / Midas)")
add("631", W("DIGICEL"), 1, "B", ref="ext. (opérateur téléphonique)")
add("611", W("MAISON DE SANTE", "MAISON MEDICALE"), 1, "B", ref="ext. (analogie Centre médical)")
add("651", W("ULYS", "AREA"), 1, "B", ref="ext. (sociétés d'autoroute / télépéage)")
add("222", W(r"(?:L )?ENTREPOT DU BRICOLAGE"), 1, ref="Entrepôt Bricolage")

# --- 129 Autres commerces alimentaires spécialisés
add("129", W("BOUCHERIES?", "BOUCHER CHARCUTIER", "CHARCUTERIE", "CHARCUTIER", "BISCUITERIE", "CAVISTE", r"FROMAG(?:ER|ERIE|ER AFFINEUR)",
             "CREMERIE", r"POISSONN?(?:ERIE|IER)", "MAREYEUR", "VOLAILLER", r"PRIMEURS?", "FRUITS ET LEGUMES", "MARCHAND DE (?:FRUITS|LEGUMES)",
             "HALLE PRIMEURS", "ROTISSERIE", "TORREFACTEUR", "BRULERIE", "NESPRESSO", "BOUTIQUE THE", "ESPACE FRAICHEUR", "COURS DES HALLES",
             "PRIMEUR BIO", "COMMERCE BIO"),
    ref="Boucherie, Charcuterie, Caviste, Fromager(ie), Crèmerie, Poissonnerie, Mareyeur, Volailler, Primeur, Fruits et légumes, Rôtisserie, Nespresso...")
add("129", X(r"(?:LE |UN )?BOUCHER", "COMMERCE DE DETAIL"), ref="Boucher, Commerce de détail")

# --- 211 Grands magasins
add("211", W("BHV", r"GALERIES? LAFAYETTE", "GRAND MAGASIN", "NOUVELLES GALERIES", "STOKOMANI", "NOZ", "PRINTEMPS GALERIE LAFAYETTE"),
    ref="BHV, Galeries Lafayette, Grand magasin, Nouvelles Galeries, Stokomani, Noz")
add("211", X(r"LE BON MARCHE(?: PARIS)?", "PRINTEMPS", "ACTION", "MAGASIN ACTION"), ref="Le Bon Marché, Printemps, Action")

# --- 212 Magasins populaires
add("212", W("GIFI", "BAZAR", "ANTIQUAIRE", "DEPOT VENTE", "EMMAUS", "SOLDERIE", "EUROCASH", r"C ?2 ?EUROS", "C 2 EUROS", r"MAGASIN (?:DE )?DESTOCKAGE"),
    ref="Gifi, Bazar, Antiquaire, Dépôt vente, Emmaus, Solderie, Eurocash, C'2 Euros, Magasin destockage")

# --- 221 Grandes surfaces spécialisées (culture, sport, loisirs)
add("221", W(r"AU VIEUX CAMPEUR", r"BUREAU ?VALLEE", "CULTURA", "DECATHLON", "FNAC", r"GO ?SPORT", r"(?:LA )?GRANDE RECRE", r"INTER ?SPORT",
             r"JOUE ?CLUB", "JOUET ?CLUB", "KING JOUET", "OFFICE DEPOT", "SPORT 2000", r"TOYS ?R ?US"),
    ref="Au Vieux Campeur, Bureau-Vallée, Cultura, Decathlon, Fnac, Go Sport, Grande Récré, Intersport, Joué Club, King Jouet, Office Dépôt, Sport 2000, Toys R Us")

# --- 222 Grandes surfaces spécialisées (maison, bricolage, jardin, animaux)
add("222", W("ALINEA", "ANIMALIS", "BOTANIC", "BOUCHARA", r"BRICO ?DEPOT", "BRICOT DEPOT", "BRICOMAN", "BRICOMARCHE", "BRICORAMA", "BRICO CASH",
             "CASTORAMA", "CENTRAKOR", "CONFORAMA", "DARTY", "ELECTRO DEPOT", "ELECTROMENAGER", "ENTREPOT BRICOLAGE",
             r"GAMM?E? ?VERT", "GEDIMAT", "HEMA", "IKEA", "JARDILAND", "JARDINERIES?", r"LAPEYRE", r"LEROY ?MERLIN", r"M\.? ?BRICOLAGE", r"MR\.? ?BRICOLAGE",
             "MAISON DU MONDE", "MAXI ZOO", "PIER IMPORT", "POINT VERT", "PROVENCE OUTILLAGE", "SAINT MACLOU", "TRUFF?AUL?T",
             "VILLAVERDE", "VIMA", "WELDOM", "ZODIO", "ZOLPAN", "ZOO PASSION", "GSS JARDIN", "GRANDE SURFACE DE BRICOLAGE"),
    ref="Alinea, Animalis, Botanic, Brico Dépôt, Castorama, Conforama, Darty, Electro Dépôt, Gamm Vert, Jardiland, Leroy Merlin, Mr Bricolage, Weldom, Ikea...")
add("222", X("PRO ET CIE", "BRICOLAGE", "BUT", "CASA", "FLY", "HABITAT", "POINT P", r"POINT P .*"), ref="Bricolage, But, Casa, Fly, Habitat, Point P (exacts : mots courants)")

# --- 223 Grandes surfaces d'habillement
add("223", W("GEMO", r"H ET M", "KIABI", "ORCHESTRA", "PRIMARK", "UNIQLO", "ZARA", "ZEEMAN", r"MARKS (?:ET |AND )?SPENCER", "GSS CHAUSSURES"),
    ref="Gémo, H&M, Kiabi, Orchestra, Primark, Uniqlo, Zara, Zeeman, Marks & Spencer, Gss chaussures")
add("223", X(r"C ET A", r"LA HALLE(?: CHAUSSURES)?"), ref="C&A, La Halle, La Halle Chaussures")

# --- 229
add("229", W("AUBERT", "AUTOUR DE BEBE", "NATALYS", r"VERT ?BAUDET"), ref="Aubert, Autour De Bébé, Natalys, Vertbaudet")

# --- 231 Commerces de détail culture, loisirs, presse
add("231", W("APPLE STORE", "ARMURERIE", "CARTERIE", "FRANCE LOISIRS?", r"GIBERT (?:JEUNE|JOSEPH)", "JOUR DE FETE", r"KIOSQUES?(?: A JOURNAUX)?",
             "LIBRAIRE", "LIBRAIRIE", r"LOISIRS (?:ET )?CREATION", "LUTHIER", r"MAISON (?:DE )?(?:LA )?PRESSE", "MARCHAND DE JOURNAUX",
             "MICROMANIA", "NATURE ET DECOUVERTE", "OXYBUL", "PAPETERIE", "PAPETRIE", "RELAIS H", "RELAY"),
    ref="Apple Store, Armurerie, Carterie, France Loisirs, Gibert, Kiosque, Librairie, Luthier, Maison de la Presse, Micromania, Nature et Découverte, Papeterie, Relay...")
add("231", X("APPLE", "PRESSE", "LA PRESSE", "TELEPHONIE"), ref="Apple, Presse, Téléphonie")

# --- 232 Autres commerces spécialisés
add("232", W("ANIMALERIE", "CUISINISTE", "DROGUERIE", "FLEURISTES?", "INTERFLORA", "MONCEAU FLEURS", "PISCINISTE", "QUINCAILLERIE"),
    ref="Animalerie, Cuisiniste, Droguerie, Fleuriste, Interflora, Monceau Fleurs, Pisciniste, Quincaillerie")
add("232", X("DECORATION"), ref="Décoration")

# --- 233 Commerces d'habillement, chaussures, optique, beauté, bijoux
add("233", W("ADIDAS", "AFFLELOU", "ANTONELLE", r"ARMAND THI(?:E|ER)RY", "AUDIOPROTHESISTE", r"BEAUTY SUC+ESS", "BENETTON", "BERSHKA",
             r"BESSON CHAUSSURES", "BIJOUTERIE", "BIJOUTIER", "BONOBO", r"BOUTIQUE (?:DE )?(?:MODE|VETEMENTS?|FEMME)", "BURTON", "CALZEDONIA",
             "CAMAIEU", "CAROLL", "CELIO", "CHAUSSEA", "CHAUSSEUR", r"CLAIRE S", "CLAIRE ACCESSOIRE", "CLEOR", r"COMMERCE (?:CHAUSSURES|HABILLEMENT)", "COURIR",
             "CYRILLUS", "DAMART", "DARJEELING", "DESIGUAL", "DEVRED", "DU PAREIL AU MEME", "ERAM", "ETAM", "EURODIF", "FOOT LOCKER", "FRIPERIE",
             "GEOX", "GRAIN DE MALICE", "HERBORISTERIE", r"HISTOIRE D OR", "HOLLISTER", "HORLOGER", "HORLOGERIE", "IKKS", r"JENN(?:IFER|YFER)",
             "KAPORAL", "KOOKAI", "KRYS", r"L OCCITANE(?: EN PROVENCE)?", r"OCCITANE", "LACOSTE", "MARIONNAUD", "MAROQUINERIE", "MEPHISTO", "MERCERIE",
             "MONDIAL TISSU", "MS MODE", "NAF NAF", "NEW LOOK", "NIKE", "NOCIBE", "OKAIDI", "OPTIC 2000", "OPTICAL CENTER", "OPTICIEN", "PANDORA",
             "PANTASHOP", "PARAPHARMACIE", "PARASHOP", "PARFUMERIE", "PETIT BATEAU", "PIMKIE", "PRET A PORTER", r"PRINCESSE TAM ?TAM", "PROMOD",
             r"PULL (?:ET |AND )?BEAR", "QUATER BACK", "QUIKSILVER", "REDSKIN", "SEBASTIANO", "SEPHORA", "SERGENT MAJOR", r"TAPE A L OEIL",
             "UNDIZ", "VERO MODA", "YVES ROCHER", "ACCESSOIRES MODE", "BEAUTY SUCCESS", "HALLE VETEMENTS?"),
    ref="Adidas, Afflelou, Bijouterie, Boutique de mode, Camaieu, Celio, Etam, Kiabi..., Opticien, Parfumerie, Pimkie, Promod, Sephora, Yves Rocher, Zara...")
add("233", X("HABILLEMENT", "ANDRE", "BRICE", "DIM", "GAP", "JULES", "MANGO", "MORGAN", r"1 ?2 ?3", "MIM", "BESSON", r"LA HALLE AUX? VETEMENTS?", r"LA HALLE VETEMENTS?"),
    ref="André, Brice, Dim, Gap, Jules, Mango, Morgan, 123, Mim (libellés exacts, mots courants)")
add("233", W("CHAUSSURES?"), NIVEAU_FAIBLE, ref="Commerce chaussures, Magasin chaussure(s) (mot seul : faible)")

# --- 234 Pharmacies
add("234", W("PHARMACIE"), NIVEAU_FAIBLE, ref="Pharmacie")

# --- 235 Stations-service
add("235", W("AVIA", "ESSO", "SHELL", "ELF", "ESSENCE", r"POMPE A ESSENCE", "STATION CARBURANT"), ref="Avia, Esso, Shell, Elf, Essence, Pompe à essence")
add("235", X("TOTAL", "TOTAL ?ENERGIES?"), ref="Total")
add("235", r"^(?:STATION )?TOTAL ?(?:ENERGIES?|ACCESS)\b(?!.*\b(?:ELECTRICITE|GAZ)\b)", 1, "B", ref="ext. (TotalEnergies = Total ; Total Access)")
add("235", r"^TOTAL(?! ELECTRIQUE| ENERGY| GAZ| WINE)(?: [A-Z0-9]+){1,4}$", NIVEAU_FAIBLE, "B", ref="ext. (Total + lieu = station)")
add("235", W("ENGEN", r"(?<!PIZZA )VITO(?! PIZZA)"), 1, "B", ref="ext. (marques de carburant DOM)")

# --- 239 Autres commerces de détail
add("239", W("BOUTIQUE (?:CADEAUX|SOUVENIRS?)", "BURALISTE", "BUREAU DE TABAC", "COMBUSTIBLE", "LIVRAISON (?:DE )?FIOUL", "PIECES AUTO", "MAGASIN PIECES AUTO"),
    ref="Boutique cadeaux/souvenirs, Buraliste, Bureau de tabac, Combustible, Livraison fioul, Magasin pièces auto")
add("239", W("TABAC"), ref="Tabac (en conflit avec un autre mot -> ambigu)")

# --- 311 / 312 / 313 / 314 / 315 Vente à distance
add("311", W("AMAZON", r"C ?DISCOUNT", "RUE DU COMMERCE", "VENTE PRIVEE"), ref="Amazon, Cdiscount, Rue Du Commerce, Vente Privée")
add("311", X("WISH"), ref="Wish")
add("314", W(r"3 SUISSES", "MAXIMO", "ATLAS FOR MEN", "BECQUET", "BOFROST", "DAXON", "LA REDOUTE", "LOCAVOR", "VALRHONA", r"WANIMO"),
    ref="3 Suisses, Atlas For Men, Becquet, Bofrost, Daxon, La Redoute, Locavor, Valrhona, Wanimo")
add("315", W(r"LE ?BON ?COIN"), ref="Le Bon Coin")

# --- 322 Marchés, brocantes
add("322", W("BRADERIE", "BROCANTE", "FOIRE", "MARCHAND AMBULANT"), ref="Braderie, Brocante, Foire")
add("322", W("FOIRE EXPO(?:SITION)?"), 0, ref="Foire expo, Foire Exposition")
add("322", X("MARCHE", "HALLES"), ref="Marché, Halles (libellés exacts uniquement)")
add("324", W("VIDE GRENIER"), ref="Vide grenier")
add("324", X(r"PARTICULIERS?"), ref="Particulier")

# --- 323 Producteurs
add("323", W("AGRICULTEUR", "APICULTEUR", "HORTICULTEUR", "LAITERIE", "MARAICHER", "OSTREICULTEUR", r"PEPINIER(?:E|ISTE)",
             "PRODUITS FERMIERS", "VENTE DIRECTE", "VITICULTEUR"),
    ref="Agriculteur, Apiculteur, Fermier, Horticulteur, Laiterie, Maraîcher, Ostréiculteur, Pépinière, Produits fermiers, Vente directe, Viticulteur")
add("323", X(r"(?:LA |LE )?FERME", "PRODUCTEURS?", "FERMIER"), ref="Ferme, Producteur (libellés exacts)")
add("323", r"^(?:LA |LES )?(?:FERME|PRODUCTEURS?) (?:DE|DU|DES|D) [A-Z0-9 ]+$", NIVEAU_FAIBLE, "B", ref="ext. (Ferme de/du X)")
add("323", r"^A LA FERME$", NIVEAU_MOT, "B", ref="ext.")

# --- 329 Autres commerces hors magasin
add("329", W(r"MACHINES?(?: PERSONNEL| EMPLOYEUR)? (?:A )?CAFE"), ref="Machine à café")
add("422", W("CAFE D ENTREPRISE"), 0, "B", ref="ext. (analogie Cafétéria entreprise)")
add("329", W("DUTY FREE", "DUTTY FREE", "GROSSISTE", "MACHINE A CAFE", "PHOTOMATON", "VENTE A DOMICILE"), ref="Duty free, Grossiste, Machine à café, Photomaton, Vente à domicile")
add("329", X("COOPERATIVE", "COOPERATIVE BIO", "DISTRIBUTEURS?"), ref="Coopérative, Distributeur")
add("329", W("DISTRIBUTEUR AUTOMATIQUE"), 1, "B", ref="ext.")

# --- 411 Restauration à service complet
add("411", W("BUFFALO GRILL", "COURTEPAILLE", r"CREPERIES?", "HIPPOPOTAMUS", "LA PATATERIE", "LEON DE BRUXELLES", "PIZZA DEL ARTE", "PIZZERIAS?",
             r"JAPONN?AIS", "AUTOGRILL", "RESTAURANT ASIATIQUE", "RESTAURANT CHINOIS", "RESTAURANT JAPONAIS", "RESTO ITALIEN", "RESTAURANT TRADITION"),
    ref="Buffalo Grill, Courtepaille, Crêperie, Hippopotamus, La Pataterie, Léon de Bruxelles, Pizza Del Arte, Pizzeria, Japonais, Restaurant asiatique/chinois/japonais...")
add("411", X(r"LA BOUCHERIE"), ref="La Boucherie")
add("411", W("RESTAURANTS?", "RESTO", "BRASSERIE"), ref="Restaurant, Resto, Brasserie (en conflit avec un autre mot spécifique -> ambigu)")

# --- 412 Restauration rapide
add("412", W(r"BAGUET SHOP", r"(?:LA )?BRIOCHE DOREE", "BURGER KING", "CAFETERIA", r"CAMION (?:A )?PIZZAS?", "CROISSANTERIE", r"DOMINO S(?: PIZZA)?",
             r"FAST ?FOOD", "FLUNCH", "FOOD TRUCK", "FRITERIE", "KEBAB", "KFC", r"(?:LA )?MIE CALINE", r"LE KIOSQUE A PIZZAS", r"MAC ?DONALD(?: ?S)?",
             r"MC ?DONALD(?: ?S)?", r"MAC ?DO", r"MC ?DO", "MCDO", r"PIZZA (?:A EMPORTER|HUT)", r"PIZZERIA A EMPORTER", "RESTAURANT RAPIDE", "RESTAURATION RAPIDE",
             "SANDWICHERIE", r"SNACK(?: BAR)?", "STARBUCKS", "SUBWAY", "TRAITEUR", r"TRAITEUR (?:CHINOIS|ITALIEN)", "CHEZ PAUL"),
    ref="Burger King, Cafétéria, Camion pizza, Croissanterie, Domino's, Fast food, Flunch, Food Truck, Friterie, Kebab, KFC, McDonald's, Pizza Hut, Sandwicherie, Snack, Starbucks, Subway, Traiteur...")
add("412", X("PAUL", "QUICK", r"(?:LE )?GREC"), ref="Paul, Quick, Grec (libellés exacts)")
add("412", W("A EMPORTER"), 1, "B", ref="ext. (Pizza/Pizzeria à emporter)")

# --- 413 Débits de boissons
add("413", W(r"BAR A VINS?", "BISTROT?", "BUVETTE", "GLACIER", r"PUB", "SALON DE THE", "BAR BRASSERIE", "BAR RESTAURANT"),
    ref="Bar à Vin, Bistro, Bistrot, Buvette, Glacier, Pub, Salon de thé")
add("413", X(r"(?:LE |LA |AU )?CAFE", r"(?:LE |LA |AU )?BAR", "CAFE BAR", "BAR CAFE"), ref="Café, Bar, Café bar, Bar Café (libellés exacts)")
add("413", r"^(?!.*\b(?:MACHINES?|CANTINE|SNACK)\b)(?:(?:LE |LA |L |AU )?(?:BAR|CAFE) [A-Z0-9 ]+|[A-Z0-9 ]+ (?:BAR|CAFE))$", NIVEAU_MOT, "B", ref="ext. (BAR / CAFE en début ou fin de nom)")
add("659", W(r"P ?M ?U", "PMU"), ref="Pmu")

# --- 421 / 422 Restauration collective
add("422", X("CANTINE"), ref="Cantine")

# --- 511 / 512 / 519 / 521 / 522 Hébergement
add("511", W("IBIS"), ref="Ibis")
add("511", X(r"(?:L )?HOTEL"), ref="Hôtel")
add("511", W("HOTEL"), NIVEAU_FAIBLE, "B", ref="ext. (HOTEL dans le nom)")
add("512", r"\bCAMPING\b(?! ?CAR)", ref="Camping")
add("519", W("GITES?"), ref="Gîte")
add("521", X(r"CITE U(?:NIVERSITAIRE)?"), ref="Cité U")
add("522", W("EHPAD", "MAISON DE RETRAITE"), ref="Ehpad, Maison de retraite")

# --- 611 Santé
add("611", W("ACUPONCTEUR", "ANESTHESISTE", "ANGIOLOGUE", "CARDIOLOGUE", "CHIRURGIEN", "CLINIQUE", "POLYCLINIQUE", "DENTISTE", "DERMATOLOGUE",
             "DOCTEUR", "ENDOCRINOLOGUE", "GENERALISTE", r"GYNECO(?:LOGUE)?", "HOPITAL", "INFIRMIERS?", "INFIRMIERES?", r"KINE(?:SITHERAPEUTE)?",
             "MEDECIN", "MEDECIN GENERALISTE", "OPHTALMO", "OPHTALMOLOGI(?:E|STE|QUE)", "OPHTALMOLOGUE", "ORL", "ORTHODONTISTE", "ORTHOPHONISTE",
             "OSTEOPATHE", "PEDIATRE", "PEDICURE", "PODOLOGUE", "PSYCHOLOGUE", "RADIOLOGIE", "RADIOLOGUE", "RHUMATOLOGUE", "SAGE FEMME",
             "LABORATOIRE MEDICAL", "CABINET MEDICAL"),
    ref="Médecin, Docteur, Dentiste, Clinique, Hôpital, Infirmier, Kiné, Radiologue, Ostéopathe, Pédiatre, Podologue, Psychologue, Sage femme...")
add("611", X("SOINS", "LABORATOIRE"), ref="Soins, Laboratoire")
add("611", W("PSYCHIATRE", "UROLOGUE", "NEUROLOGUE", "PNEUMOLOGUE", r"GASTRO ?ENTEROLOGUE", "DIETETICIENNE?", "ORTHOPTISTE"), 1, "B", ref="ext. (analogie)")
add("611", r"^DR [A-Z]+(?: [A-Z]+){0,3}$", NIVEAU_FAIBLE, "B", ref="ext. (DR <nom> = médecin)")
add("611", r"^CHU(?: [A-Z]+){0,2}$(?<!RESTAU)|^CENTRE HOSPITALIER\b", 1, "B", ref="ext. (analogie Hôpital)")

# --- 619
add("619", W("AIDE A DOMICILE", "SERVICE A LA PERSONNE"), ref="Aide à domicile, Service à la personne")

# --- 621 Culture
add("621", W("BIBLIOTHEQUE", "CGR", r"CINEMAS?", "CINE", "ESPACE CULTUREL", "EXPOSITION", "GAUMONT", "MEDIATHEQUE", "MJC", "MUSEE", "OPERA",
             "PATHE", r"SALLE (?:DE )?SPECTACLES?", "SPECTACLE", "THEATRE", "UGC", "ZENITH"),
    ref="Bibliothèque, CGR, Cinéma, Espace culturel, Gaumont, Médiathèque, MJC, Musée, Opéra, Pathé, Salle de spectacle, Théâtre, UGC, Zenith")

# --- 622 Sports, loisirs
add("622", W("AQUAGYM", "BOITE DE NUIT", "BOWLING", "CENTRE AQUATIQUE", "CENTRE EQUESTRE", "CIRQUE", "CLUB BRIDGE", r"CLUB (?:DE )?(?:FOOT|SPORT|TENNIS)",
             "CLUB GYM", "CLUB HOUSE", "CLUB SPORTIF", "DANCING", "DANSE", "DISCOTHEQUE", "FORFAIT SKI", "GOLF", "GYMNASE", "KARTING",
             "LASER GAME", "MANEGE", r"PARC (?:ANIMALIER|AQUATIQUE|ATTRACTIONS?|D ATTRACTIONS?|LOISIRS|ZOOLOGIQUE)", "PATINOIRE", "PISCINE MUNICIPALE",
             "PONEY CLUB", r"SALLE (?:DE )?(?:GYM|JEUX|SPORT)", "SQUASH", "STADE", r"STATION (?:DE )?SKI", "PARC ZOOLOGIQUE"),
    ref="Aquagym, Boite de nuit, Bowling, Cirque, Club de foot/sport/tennis, Dancing, Danse, Discothèque, Disney, Golf, Gymnase, Karting, Manège, Piscine, Poney Club, Stade, Zoo...")
add("622", X("DISNEY", "DISNEYLAND", "DISNEYLAND PARIS", "DISNEY VILLAGE"), ref="Disney, Disneyland")
add("622", X("ZOO", "LE ZOO", "PARC ZOOLOGIQUE"), ref="Zoo (exact ; ZOO dans Maxi Zoo/Zoo Passion = animalerie)")
add("622", r"^(?:LA |LE )?PISCINES?\b(?! PLUS| DESJOYAUX)", ref="Piscine (en début de libellé : sinon pisciniste)")
add("622", W("FOOTBALL"), NIVEAU_FAIBLE, "B", ref="ext. (Club de foot)")

# --- 623 Soins personnels
add("623", r"^(?!.*\b(?:" + MOTS_COMMERCE + r")\b).*" + W("BODY MINUTE", "CENTRE DE BEAUTE", "COIFFEURS?", "COIFFEUSES?", "COIFFURE", "ESTHETICIENNE", "FABIO SALSA", "FRANCK PROVOST", "INSTITUT DE BEAUTE",
             "MANUCURE", "ONGLERIE", "SALON DE BEAUTE", "SALON DE COIFFURE", "SALON ESTHETIQUE", "TATOUEUR"),
    ref="Body Minute, Coiffeur, Coiffure, Esthéticienne, Franck Provost, Institut de beauté, Onglerie, Salon de coiffure, Tatoueur...")
add("623", W("BARBIER", r"BARBER ?SHOP"), 1, "B", ref="ext.")

# --- 631 Télécom
add("631", W("BOUYGUES", r"CANAL (?:PLUS|SATELLITE)", "FRANCE TELECOM", "GROUPE ORANGE", "OPERATEUR TELEPHONE", "SFR"),
    ref="Bouygues, Canal Plus/Satellite, France Telecom, Groupe Orange, Opérateur Téléphone, SFR")
add("631", X("CANAL", "FREE", "ORANGE"), ref="Canal +, Free, Orange (libellés exacts)")
add("631", W("FREE MOBILE", "FREE TELECOM", "SOSH", "ORANGE REUNION"), 1, "B", ref="ext.")

# --- 632 Artisans
add("632", W("ARTISAN ELECTRICIEN", "ARTISAN MACON", "ARTISAN PEINTRE", "ARTISAN PLOMBIER", "CHAUFFAGISTE", "ELECTRICIEN",
             "MENUISERIE", "PLOMBERIE", "RAMONEUR", "SERRURERIE", "SERRURIER"),
    ref="Artisan électricien/Maçon/Peintre/plombier, Chauffagiste, Electricien, Jardinier, Menuiserie, Plomberie, Ramoneur, Serrurerie")
add("632", X("ARTISAN", "ELECTRICITE", "JARDINIER"), ref="Artisan, Electricité, Jardinier (exacts)")
add("632", W("PLOMBIER", "MACON", "PEINTRE", "MENUISIER"), 1, "B", ref="ext. (analogie)")

# --- 641 Automobile
add("641", W("AUTOSUR", "AUTOVISION", r"CARTER ?CASH", "CASSE AUTO", "CENTRE AUTO", "CITROEN", "CONCESSIONNAIRE", "CONTROLE TECHNIQUE", "ELEPHANT BLEU",
             "EUROMASTER", "FEU VERT", "FORD", "GARAGES?", "GARAGISTE", "HERTZ", "HONDA", r"LOCATION (?:DE )?VOITURE", "MAGASIN AUTO", "MECANICIEN",
             "MIDAS", "NORAUTO", "PEUGEOT", "RENAULT", "SECURITEST", "SERVICE AUTO", "SPEEDY", "YAMAHA"),
    ref="Autosur, Autovision, Carter-Cash, Casse auto, Citroen, Concessionnaire, Contrôle technique, Feu Vert, Ford, Garage, Hertz, Honda, Midas, Norauto, Peugeot, Renault, Speedy, Yamaha")
add("641", W("TOYOTA", "VOLKSWAGEN", "NISSAN", "OPEL", "BMW", "MERCEDES", "AUDI", "DACIA", "KIA", "HYUNDAI", "SUZUKI", "CARROSSERIE", "FIAT", "MAZDA"), 1, "B", ref="ext. (autres marques/garages)")

# --- 649 Services divers
add("649", W(r"5 A SEC", "CORDONNERIE", "CORDONNIER", "COUTURIERE", "LAVERIE", "LAVERIE AUTOMATIQUE", "LAVOMATIC", "MISTER MINIT", "PRESSING"),
    ref="5 à Sec, Cordonnerie, Cordonnier, Couturière, Laverie, Lavomatic, Mister Minit, Pressing")
add("649", W("TEINTURERIE", "BLANCHISSERIE"), 1, "B", ref="ext.")

# --- 651 Transports
add("651", W("AIR FRANCE", "ASF", "AUTOBUS", "AUTOCAR", "BLABLACAR", "BUS", "COFIROUTE", "COVOITURAGE", "EASY JET", "GARE ROUTIERE", "GARE SNCF",
             "GUICHET BUS", "GUICHET SNCF", r"(?:TELE)?PEAGES?", "RATP", "SANEF", "SNCF", r"SOCIETE D AUTOROUTES?", "SOCIETE DE TRANSPORT", "TAXI", "TGV", "TRAIN",
             "TRAMWAY", r"TRANSPORTS? (?:EN COMMUN|URBAINS?)", "TRANSPORTEUR", "VELIB", r"VINCI AUTOROUTES?", "UBER"),
    ref="Air France, Autobus, Autocar, Blablacar, Cofiroute, Gare routière, Péage, Ratp, Sanef, Sncf, Taxi, Tgv, Train, Tramway, Uber, Vélib, Vinci Autoroute...")
add("651", X("AVION", "GARE", r"TRANSPORTS?", "AUTOROUTES?", r"SERVICE DES AUTOROUTES"), ref="Gare, Transport, Autoroute (libellés exacts)")
add("651", W("AUTOROUTES?"), NIVEAU_FAIBLE, ref="Autoroutes (mot seul : faible)")
add("651", W("APRR"), 1, "B", ref="ext. (société d'autoroutes)")

# --- 652 Administrations, services publics
add("652", W("ADMINISTRATION", "BUREAU DE POSTE", "CENTRE DES IMPOTS", "COMPAGNIE DES EAUX", "EDF", "ENGIE", "FACTURE EAU", "GARDERIE", "GDF", "IMPOTS", "LA POSTE",
             "LYCEE", "MAIRIE", "PREFECTURE", "SERVICE DES EAUX", "TRESOR PUBLIC", "TRESORERIE", "UNIVERSITE", "URSSAF", "VEOLIA", "FAC"),
    ref="Administration, Bureau de Poste, Centre des Impots, Edf, Engie, Garderie, Gdf, Impôts, La Poste, Lycée, Mairie, Préfecture, Trésor Public, Université, Urssaf, Véolia")
add("652", X("ECOLE", "ECOLE PRIVEE", "POSTE"), ref="Ecole, Ecole privée, Poste")
add("652", W("COLLEGE", "HOTEL DE VILLE"), 1, "B", ref="ext. (collège ; hôtel de ville)")
add("652", r"^(?:L )?ECOLE\b(?! DE | DIRECTE)", NIVEAU_FAIBLE, "B", ref="ext. (ECOLE en début de libellé)")
add("329", r"^(?!.*\b(?:BANCAIRE|EPARGNE|RETRAIT|BILLETS?|CB|BOULANGER|PIZZA|PAIN)\b).*\bDISTRIBUTEURS?\b", NIVEAU_MOT, "B", ref="ext. (Distributeur = distributeur automatique ; en conflit -> ambigu)")
add("622", W("GROUPAMA STADIUM"), 0, "B", ref="ext.")
add("511", W(r"HOTEL (?:ET |& )?SPA"), 0, "B", ref="ext.")
add("622", W("ECOLE DE SKI"), 0, "B", ref="ext. (analogie Forfait ski / Station de ski)")

# --- 653 Banques, assurances
add("653", W("ALLIANZ", "ASSURANCES?", "ASSUREUR", "AXA", "BANQUES?", "BNP", r"CAISSE D? ?EPARGNE", "CETELEM", "CIC", "COFIDIS", "CREDIT AGRICOLE", "CREDIT MUTUEL",
             "DIRECT ASSURANCE", "GMF", "GROUPAMA", "MAAF", "MACIF", "MAIF", "MATMUT", "MGEN", "MMA", "MUTUELLE", "NATIXIS", "ORGANISME FINANCIER"),
    ref="Allianz, Assurance, Axa, Banque, BNP, Caisse d'Epargne, Cetelem, CIC, Cofidis, Crédit Agricole, Crédit Mutuel, GMF, Groupama, Maaf, Macif, Maif, Matmut, Mgen, MMA, Mutuelle, Natixis")
add("653", W("BANQUE POPULAIRE", "LCL", "SOCIETE GENERALE", "CREDIT DU NORD", "CREDIT LYONNAIS", "HSBC", "BOURSORAMA", "CREDIT COOPERATIF"), 1, "B", ref="ext. (autres banques)")

# --- 659 Autres services
add("659", W("AGENCE (?:DE )?VOYAGES?", "AGENCE IMMOBILIERE", "BAILLEUR", r"BILLET+ERIE", "CRECHE", "CROIX ROUGE", "EGLISE", "FRANCAISE DES JEUX",
             "GROUPE VINCI", "HUISSIER", "LOCATION SKI", "LOTO", "NOTAIRE", "NOURRICE", "PARKING", "PHOTOGRAPHE", "SECOURS CATHOLIQUE", "SYNDIC", "SYNDICAT",
             "TOILETTEUR", "VETERINAIRES?", "COURS A DOMICILE", "COMITE D ENTREPRISE"),
    ref="Agence de voyage, Agence immobilière, Billetterie, Crèche, Croix Rouge, Eglise, Française des Jeux, Huissier, Loto, Notaire, Nourrice, Parking, Photographe, Secours Catholique, Spa, Syndic, Toiletteur, Vétérinaire...")
add("659", X("DON", "DON ARGENT", "SPA"), ref="Don, Don argent, Spa")
add("659", W("FDJ", "CSE", "SECOURS POPULAIRE", "BANQUE ALIMENTAIRE"), 1, "B", ref="ext. (analogie)")
add("659", W("ASSOCIATION"), NIVEAU_FAIBLE, "B", ref="ext. (Association : mot seul faible)")


# --------------------------------------------------------------------------------------
# Hors champ : libellés qui ne sont pas une enseigne (pas de recherche web utile)
# --------------------------------------------------------------------------------------
# Règles EXACTES uniquement (le libellé entier) : "A DOMICILE" est hors champ, mais "COIFFEUR A DOMICILE"
# reste codé 623. Ces règles ne viennent pas de la liste annotée : elles écartent ce qui n'est pas un point de vente.
CODE_HORS_CHAMP = "00"
HORS_CHAMP_LABEL = "Hors champ (pas une enseigne)"  # type de magasin affiché pour le code 00
add(CODE_HORS_CHAMP, X(
    # illisible / vide / sans information
    "", "ILLISIBLE", "PAS DE TICKET", "SANS TICKET", "DUPLICATA", "ENSEIGNE", "L S", "LS", "AUTRE", "DIVERS",
    # modes de paiement et opérations bancaires
    r"VIREMENTS?(?: BANCAIRE| MENSUEL)?", r"PRELEVEMENTS?(?: BANCAIRE| MENSUEL| AUTOMATIQUE)?", "CB", "DAB", r"CHEQUES?", "CARTE",
    "ARGENT LIQUIDE", "ESPECES", "RETRAIT", "PAR MOIS",
    # lieu ou situation sans enseigne
    "A DOMICILE", "DOMICILE", "SUR LA ROUTE", "AU BORD DE LA ROUTE", r"BORD DE (?:LA )?ROUTE", "ROUTE", "DANS LA RUE", "DANS LE QUARTIER",
    "VILLE", "EN VILLE", "TRAVAIL", "PRIVE", "PROPRIETAIRE", "COURSES", "SUR PLACE",
    # termes génériques sans marque
    "BOUTIQUE", "MAGASIN", "PETIT MAGASIN", r"PETITE? BOUTIQUE", "MAGASIN SPECIALISE", "SITE SPECIALISE", "COMMERCE DE PROXIMITE", "COMMERCE PROXIMITE",
    "COMMERCE DE QUARTIER", "PETIT COMMERCE", r"BOUTIQUE (?:DU )?QUARTIER", "BOUTIQUE INDEPENDANTE", "COMMERCANT", "MAGASIN COMMERCANT",
    "VENDEUR", "VENDEUSE", "INDEPENDANT", "ENTREPRISE", r"CENTRE COMMERCIALE?",
), 0, ref="hors champ : libellé qui n'est pas une enseigne (décision utilisateur)")

# --------------------------------------------------------------------------------------
# Compléments issus de la relecture des non codés les plus fréquents
# --------------------------------------------------------------------------------------
# --- Variantes d'orthographe de marques de la liste annotée (même code) : tier A
add("312", W(r"AUCHAN ?DRIVE"), 0, ref="Auchan Drive")
add("659", W(r"EASY ?PARK", "HORODATEURS?"), 1, "B", ref="ext. (analogie Parking)")
add("222", W(r"MAXI ?ZOO", r"BRICO ?CASH", r"BRICO ?MARCHE", "MONSIEUR BRICOLAGE", r"MAISONS? DU MONDE", r"WELDO[MN]", r"BRICOT? ?DEPOT"),
    ref="Maxi Zoo, Brico Dépôt, Bricomarché, Mr Bricolage, Maison Du Monde, Weldom (variantes d'écriture)")
add("222", X("BRICO"), ref="Bricolage (forme abrégée)")
add("112", W(r"SUPER ?MARCHES?"), ref="Supermarché (variante d'écriture)")
add("114", W(r"HUIT A HUIT"), ref="8 à Huit (écrit en toutes lettres)")
add("231", W(r"NATURE ET DECOUVERTES?"), ref="Nature Et Découverte")
add("221", W(r"BUREAU ?VALL?EE"), ref="Bureau-Vallée")
add("412", W(r"BAGUET(?:TE)? SHOP", r"(?:LA )?BRIOCHE DOREE?"), ref="Baguet Shop, Brioche Dorée")
add("411", W(r"(?:PIZZA )?DEL ARTE"), ref="Pizza Del Arte")
add("233", W(r"MONDIAL TISSUS?"), ref="Mondial Tissu")

# --- Enseignes connues absentes de la liste annotée : tier B (code choisi par analogie avec une enseigne annotée)
add("212", W("B ET M", r"TEDI(?: DISTRIBUTION)?", r"BAZAR(?:LAND|DIER)"), 1, "B", ref="ext. (analogie Gifi, Foir'Fouille, Bazar)")
add("222", W(r"4 MURS", "JYSK", "BIG MAT"), 1, "B", ref="ext. (analogie Conforama, Alinea, Gedimat)")
add("233", W("ADOPT", r"AROMA ?ZONE", r"KIKO(?: MILANO)?", r"DM DROGERIE MARKT", "LUSH", "RITUALS", "NEW YORKER", r"LEVI S", "SKECHERS", "STRADIVARIUS",
             "DEICHMANN", "GRAND OPTICAL", r"MEGA ?CHAUSS", "LC WAIKIKI", "LEFTIES", "PSYCHO BUNNY"),
    1, "B", ref="ext. (analogie Sephora, Nike, Bershka, Eram, Optical Center)")
add("233", X("COS"), 1, "B", ref="ext. (analogie H&M)")
add("221", W("SMYTHS(?: TOYS)?", "LUDICLUB"), 1, "B", ref="ext. (analogie King Jouet, Joué Club)")
add("221", X("LEGO", "LEGO STORE"), 1, "B", ref="ext.")
add("231", W("FURET DU NORD"), 1, "B", ref="ext. (analogie Librairie)")
add("311", W("SHEIN", "TEMU", r"MANO ?MANO"), 1, "B", ref="ext. (analogie Amazon, Cdiscount)")
add("315", W("VINTED"), 1, "B", ref="ext. (analogie Le Bon Coin)")
add("112", W("MERCADONA"), 1, "B", ref="ext. (analogie supermarchés)")
add("121", W("BANETTE", "MARIE BLACHERE", "LA PANETIERE", "PATAPAIN", "LINDT"), 1, "B", ref="ext. (analogie Paul, Leonidas : boulangerie / chocolatier)")
add("129", W(r"V (?:ET|AND) B", "COMPTOIR DES VIGNES", "COMTESSE DU BARRY", "ARTISAN BOUCHER", "ROTISSEUR"), 1, "B", ref="ext. (analogie Caviste, Boucherie)")
add("129", X("LEGUMIER", "LEGUMERIE", "FRUITIER"), 1, "B", ref="ext. (analogie Primeur, Marchand de légumes)")
add("511", X(r"B ET B(?: HOTELS?)?"), 1, "B", ref="ext. (B&B Hôtels)")
add("411", W(r"(?:LES )?3 BRASSEURS", r"LE CHIQUITO", "CHIQUITO"), 1, "B", ref="ext. (analogie Brasserie, Buffalo Grill)")
add("412", W("BYRON BURGER", "M TACOS", "TACOS FACTORY", "KRISPY KREME", "POKAWA", "EAT SALAD"), 1, "B", ref="ext. (analogie Burger King, Quick, Subway)")
add("413", W("AMORINO", "COLUMBUS CAFE"), 1, "B", ref="ext. (analogie Glacier, Café)")
add("621", W("KINEPOLIS"), 1, "B", ref="ext. (analogie Pathé, UGC, Gaumont)")
add("622", W("BASIC FIT", "FITNESS PARK", "PALAIS DES SPORTS", "BOULODROME"), 1, "B", ref="ext. (analogie Salle de sport, Stade)")
add("631", W("CANAL ANTILLES?", "OUTREMER TELECOM", "REGLO MOBILE"), 1, "B", ref="ext. (analogie Canal +, SFR, Free)")
add("651", W("FLIXBUS", "ESCOTA", "KEOLIS", "DIVIA", "NAVIGO", "HEETCH", "TRANSAVIA", "COMPAGNIE AERIENNE", "CAR JAUNE"), 1, "B",
    ref="ext. (analogie Air France, Ratp, Vinci Autoroute, Blablacar)")
add("651", X("BOLT", "TBM"), 1, "B", ref="ext. (analogie Uber)")
add("652", W("SAUR", "SUEZ", "PLENITUDE", "TOTAL ELECTRIQUE", "CCAS"), 1, "B", ref="ext. (analogie Véolia, Edf, Engie, Mairie)")
add("652", X("ANTS", "DGFP", "ENI"), 1, "B", ref="ext. (analogie Impôts, Edf)")
add("653", W("GENERALI", "PREDICA", "QONTO"), 1, "B", ref="ext. (analogie Axa, Crédit Agricole)")
add("659", W("PAYBYPHONE", "FLOWBIRD", r"HELLO ?ASSO", "PAROISSE", "PRESBYTERE", "AIRBNB", r"BOOKING(?: COM)?", "SELECTOUR"), 1, "B",
    ref="ext. (analogie Parking, Eglise, Association, Agence de voyage)")
add("659", X("ROTARY"), 1, "B", ref="ext. (association)")


# --- Libellés fréquents confirmés avec l'utilisateur
add(CODE_HORS_CHAMP, X("SEPA", "SEPA FAST", "SEPAFAST"), 0, ref="hors champ : virement SEPA, pas une enseigne (décision utilisateur)")
add("314", W("TOO GOOD TO GO"), 1, "B", ref="ext. (application de vente en ligne spécialisée ; analogie Bofrost, Valrhona)")
add("322", W(r"MARCHE (?:AUX?|DE|DES|DU) LEGUMES?"), 1, "B", ref="ext. (analogie Marché primeur(s))")
add("651", W(r"MARTINIQUE TRANSPORTS?"), 1, "B", ref="ext. (autorité de transport en commun ; analogie Ratp, Transport en commun)")
add("235", W("PETROLEC"), 1, "B", ref="ext. (analogie Petro Est / Petro Ouest E.Leclerc)")
add("211", W("DISTRICENTER"), 1, "B", ref="ext. (magasin généraliste : meubles, vêtements, accessoires ; analogie Grand magasin, Action, Noz)")


# --- Enseignes nationales proposées puis validées par l'utilisateur (tier B : code choisi par analogie avec la liste annotée)
add("121", X(r"ANGE(?: ET DELICES| LE CRES)?"), 1, "B", ref="ext. (Boulangerie Ange ; analogie Paul, Boulangerie)")
add("121", W(r"AUX MERVEILLEUX(?: DE FRED)?"), 1, "B", ref="ext. (pâtisserie ; analogie Jeff de Bruges, Leonidas)")
add("212", W(r"MAXI ?BAZAR"), 1, "B", ref="ext. (analogie Bazar, Gifi)")
add("412", W(r"CLASS ?CROUTE", r"QUICK ANTILLES?", "COJEAN", r"O ?TACOS", "FIVE GUYS"), 1, "B", ref="ext. (analogie Subway, Quick, Burger King)")
add("222", W("TOM ET CO", "MEDOR ET COMPAGNIE", "TERRANIMO"), 1, "B", ref="ext. (animaleries en grande surface spécialisée ; analogie Maxi Zoo, Animalis, Zoo Passion en 222)")
add("222", W("TOUT FAIRE MATERIAUX", "PROLIANS"), 1, "B", ref="ext. (négoce de matériaux ; analogie Point P, Gedimat)")
add("212", W("EASY CASH"), 1, "B", ref="ext. (achat-vente d'occasion ; analogie Dépôt vente, Emmaüs)")
add("233", W("COMPTOIR DES COTONNIERS", "SANDRO", "SUPERDRY", "LE TEMPS DES CERISES", "SAN MARINA"), 1, "B", ref="ext. (prêt-à-porter / chaussures ; analogie Caroll, Morgan, Promod, Eram)")
add("233", X("VANS", "BOCAGE"), 1, "B", ref="ext. (analogie Nike, Eram ; exact : mots courants)")
add("623", W("JEAN LOUIS DAVID"), 1, "B", ref="ext. (analogie Franck Provost, Fabio Salsa)")
add("622", W(r"(?:L )?ORANGE BLEUE?", "PUY DU FOU", "PARC ASTERIX", "ASTERIX", "CASINO BARRIERE"), 1, "B",
    ref="ext. (salle de sport, parcs à thème, casino de jeux ; analogie Salle de sport, Disney, Salle de jeux)")
add("641", W("AUTO SECURITAS", "TESLA", "SIXT"), 1, "B", ref="ext. (analogie Autosur, Renault, Hertz)")
add("641", r"^POINT S\b", 1, "B", ref="ext. (analogie Euromaster, Speedy)")
add("235", W("DYNEFF"), 1, "B", ref="ext. (analogie Total, Esso, Avia)")
add("235", r"^ELAN$|\bSTATION ELAN\b", 1, "B", ref="ext. (marque de carburant)")
add("651", W("OUIGO", "RYANAIR", "AIR AUSTRAL", "ATMB", "SAPN"), 1, "B", ref="ext. (analogie Sncf, Air France, Asf, Sanef)")
add("651", W(r"EASY ?JET"), 1, ref="Easy Jet (écrit en un mot)")
add("659", W("VINCI PARK", "Q PARK"), 1, "B", ref="ext. (analogie Parking, Indigo)")
add("311", W("VEEPEE", r"SHOWROOM ?PRIVE"), 1, "B", ref="ext. (Veepee = Vente Privée ; analogie Vente Privée, Wish)")
add("314", W("ZALANDO"), 1, "B", ref="ext. (e-commerce spécialisé mode ; analogie La Redoute)")
add("315", W("EBAY"), 1, "B", ref="ext. (analogie Le Bon Coin)")
add("323", W("AMAP"), 1, "B", ref="ext. (paniers de producteurs ; analogie Producteur, Vente directe)")
add(CODE_HORS_CHAMP, X("PAYPAL", "FACEBOOK PAYPAL", "SCA PAYPAL"), 0, ref="hors champ : moyen de paiement (décision utilisateur)")
add("319", W("NETFLIX", "SPOTIFY", "DEEZER", r"PLAYSTATION(?: STORE| NETWORK)?"), 1, "B", ref="ext. (abonnements numériques : autre type de site)")
add("652", W("DGFIP", "CPAM", "GAZ DE BORDEAUX", "ANTARGAZ", "OHM ENERGIE"), 1, "B", ref="ext. (analogie Impôts, Administration, Edf, Engie, Gdf)")
add("652", X("CAF"), 1, "B", ref="ext. (analogie Administration)")
add("631", W("CORIOLIS", "NRJ MOBILE", "PRIXTEL"), 1, "B", ref="ext. (analogie Sfr, Bouygues)")
add("653", W("BRED", "N26", "SOFINCO", r"SWISS ?LIFE", "PACIFICA", r"AG2R(?: LA MONDIALE)?"), 1, "B", ref="ext. (analogie Bnp, Cetelem, Cofidis, Axa, Groupama)")
add("129", X("CAVES?", r"CAVE DE VINS?"), 1, "B", ref="ext. (libellé exact 'Cave' = caviste ; analogie Caviste, Cave à vin)")
add("211", r"^NORMAL\b", 1, "B", ref="ext. (enseigne discount Normal ; analogie Action, Noz, Stokomani)")
add("653", r"^APRIL\b", 1, "B", ref="ext. (assurance ; analogie Axa)")
add("659", W("LAFORET", "FONCIA", r"PARIONS(?: SPORT)?"), 1, "B", ref="ext. (analogie Agence immobilière, Syndic, Française des jeux, Loto)")
add("611", W("BIOGROUP", "SYNLAB", "IMAGERIE MEDICALE"), 1, "B", ref="ext. (analogie Laboratoire médical, Centre radiologie)")
add("231", W("LAGARDERE"), 1, "B", ref="ext. (Lagardère Travel Retail = boutiques Relay ; analogie Relay)")
add("329", W("SELECTA"), 1, "B", ref="ext. (distributeurs automatiques ; analogie Machine à café, Distributeur)")
# Évaluation du run n300 (docs/evaluation_run_26112026_1626_29.md)
# « <métier de bouche> MARCHE » = étal sur un marché (322), pas la boutique ; MARCHE U / MARCHE FRAIS restent des supermarchés.
add("322", r"\b(?:BOULANGER(?:IE)?|BOUCHER(?:IE)?|CHARCUT(?:IER|ERIE)|POISSONN?(?:IER|ERIE)|POISSONS?|FROMAGER(?:IE)?|CREMERIE|"
           r"LEGUMES?|FRUITS?|PRIMEURS?|ROTISSERIE|VOLAILLER|TRAITEUR|HUITRES?|OLIVES?|MIEL)\s+MARCHE\b(?!\s+(?:U|FRAIS|PLUS|EXPRESS)\b)",
    0, "B", ref="ext. (analogie Boucherie marché, Fromager marché, Primeur marché)")
add("411", W("BISTRO REGENT"), 0, "B", ref="ext. (chaîne de restaurants ; prime sur BISTRO = 413)")
add("622", W("PINDER"), 1, "B", ref="ext. (Cirque Pinder ; analogie Cirque)")
add("314", W(r"MISTER ?AUTO", "AUTODOC"), 1, "B", ref="ext. (sites de pièces auto en ligne ; analogie Wanimo.com, La Redoute)")


# --------------------------------------------------------------------------------------
# Moteur
# --------------------------------------------------------------------------------------
def coder(libelle: str, tiers: tuple[str, ...] = ("A", "B")) -> dict:
    """Code un libellé.

    Renvoie {"code", "statut", "regles", "tier", "codes_candidats"} avec statut :
      - "code"   : une règle (ou plusieurs, toutes du même code) au niveau gagnant ;
      - "hors_champ" : le libellé n'est pas une enseigne (code CODE_HORS_CHAMP = "00") -> à écarter, pas au LLM ;
      - "ambigu" : plusieurs codes différents au niveau gagnant -> NON codé ;
      - "aucune" : aucune règle ne matche.
    """
    norm = normaliser(libelle)
    touchees = [r for r in REGLES if r.tier in tiers and r.motif.search(norm)]
    if not touchees:
        return {"code": None, "statut": "aucune", "regles": [], "tier": None, "codes_candidats": []}
    niveau = min(r.niveau for r in touchees)
    gagnantes = [r for r in touchees if r.niveau == niveau]
    codes = sorted({r.code for r in gagnantes})
    if len(codes) > 1:
        return {"code": None, "statut": "ambigu", "regles": [r.id for r in gagnantes], "tier": None, "codes_candidats": codes}
    # tier "A" si au moins une règle A confirme le code, sinon "B"
    tier = "A" if any(r.tier == "A" for r in gagnantes) else "B"
    statut = "hors_champ" if codes[0] == CODE_HORS_CHAMP else "code"
    return {"code": codes[0], "statut": statut, "regles": [r.id for r in gagnantes], "tier": tier, "codes_candidats": codes}
