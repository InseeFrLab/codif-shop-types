"""Génère docs/processus.excalidraw : le schéma du pipeline (entrées -> regex -> LLM -> sorties).

    uv run docs/gen_processus.py docs/processus.excalidraw

À relancer après TOUT changement du pipeline (entrées, règles, prompt, colonnes de sortie, cache…) :
on modifie les textes ici plutôt que dans Excalidraw, pour que le schéma reste reproductible.
Le script vérifie qu'aucun bloc ne se chevauche, qu'aucune flèche ne traverse un texte et que chaque
bloc reste dans sa colonne.
"""
import json
import random
import sys
import textwrap

random.seed(7)
CW16 = 8.6   # largeur moyenne d'un caractère à 16 px (Helvetica), un peu généreuse
LH = 1.25
PAD = 16
BOX_FS, ANN_FS, LAB_FS = 18, 15, 14
ANN_COLOR = "#555555"
INK = "#3a3a3a"        # traits
TXT = "#161616"        # texte
NAVY = "#000091"       # bleu institutionnel (titres)
elements, by_id = [], {}


def base(id_, type_, x, y, w, h, **kw):
    e = {"id": id_, "type": type_, "x": x, "y": y, "width": w, "height": h, "angle": 0,
         "strokeColor": INK, "backgroundColor": "transparent", "fillStyle": "solid",
         "strokeWidth": 1, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
         "groupIds": [], "frameId": None, "roundness": None, "seed": random.randint(1, 2**31),
         "version": 1, "versionNonce": random.randint(1, 2**31), "isDeleted": False,
         "boundElements": [], "updated": 1, "link": None, "locked": False}
    e.update(kw)
    elements.append(e)
    by_id[id_] = e
    return e


def tsize(text, fs):
    lines = text.split("\n")
    return max(len(l) for l in lines) * CW16 * fs / 16, len(lines) * fs * LH


def text_el(id_, text, x, y, fs, container=None, align="center", color=TXT, family=2):
    w, h = tsize(text, fs)
    return base(id_, "text", x, y, w, h, strokeColor=color, text=text, fontSize=fs, fontFamily=family,
                textAlign=align, verticalAlign="middle", containerId=container, originalText=text,
                lineHeight=LH, autoResize=True)


def box(id_, text, x, y, bg, w=240, shape="rectangle", dashed=False):
    tw, th = tsize(text, BOX_FS)
    if shape == "diamond":
        w, h = tw * 2 + 30, th * 2 + 30
    else:
        w, h = max(w, tw + 2 * PAD), th + 2 * PAD
    r = base(id_, shape, x, y, w, h, backgroundColor=bg, strokeStyle="dashed" if dashed else "solid",
             roundness=None)
    t = text_el(id_ + "_t", text, x + (w - tw) / 2, y + (h - th) / 2, BOX_FS, container=id_)
    r["boundElements"].append({"type": "text", "id": t["id"]})
    return r


def move(e, x=None, y=None):
    """Déplace une bulle et son texte."""
    t = by_id.get(e["id"] + "_t")
    dx = 0 if x is None else x - e["x"]
    dy = 0 if y is None else y - e["y"]
    e["x"] += dx; e["y"] += dy
    if t:
        t["x"] += dx; t["y"] += dy


def ann(id_, text, x, y, chars=40):
    """Explication en langage naturel, texte libre gris."""
    wrapped = "\n".join(textwrap.fill(p, chars) for p in text.split("\n"))
    return text_el(id_, wrapped, x, y, ANN_FS, align="left", color=ANN_COLOR)


def bottom(e):
    return e["y"] + e["height"]


def cy(e):
    return e["y"] + e["height"] / 2


def anchor(e, side):
    x, y, w, h = e["x"], e["y"], e["width"], e["height"]
    return {"r": (x + w, y + h / 2), "l": (x, y + h / 2), "t": (x + w / 2, y), "b": (x + w / 2, y + h)}[side]


def arrow(id_, a, sa, b, sb, label=None, dashed=False, via=None, shift=(0, 0, 0, 0), label_seg=None):
    x0, y0 = anchor(by_id[a], sa)
    x1, y1 = anchor(by_id[b], sb)
    x0, y0, x1, y1 = x0 + shift[0], y0 + shift[1], x1 + shift[2], y1 + shift[3]
    pts = [(x0, y0)] + (via or []) + [(x1, y1)]
    rel = [[px - x0, py - y0] for px, py in pts]
    xs = [p[0] for p in rel]; ys = [p[1] for p in rel]
    e = base(id_, "arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
             strokeStyle="dashed" if dashed else "solid", roundness=None, strokeWidth=1.5,
             points=rel, lastCommittedPoint=None,
             startBinding={"elementId": a, "focus": 0, "gap": 4},
             endBinding={"elementId": b, "focus": 0, "gap": 4},
             startArrowhead=None, endArrowhead="arrow", elbowed=False)
    by_id[a]["boundElements"].append({"type": "arrow", "id": id_})
    by_id[b]["boundElements"].append({"type": "arrow", "id": id_})
    if label:
        segs = list(zip(pts, pts[1:]))
        seg = segs[label_seg] if label_seg is not None else max(segs, key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
        (ax, ay), (bx, by) = seg
        tw, th = tsize(label, LAB_FS)
        t = text_el(id_ + "_t", label, (ax + bx) / 2 - tw / 2, (ay + by) / 2 - th / 2, LAB_FS, container=id_)
        e["boundElements"].append({"type": "text", "id": t["id"]})
    return e


GRAY, BLUE, LBLUE, VIOLET, LVIOLET, GREEN, YELLOW, RED = (
    "#eeeeee", "#cfdcf2", "#e8eef8", "#d9d3ee", "#ebe8f6", "#d5eedd", "#fbefc9", "#f7dcd9")
TOP = 170
G = 14          # écart bulle -> explication
STEP = 60       # écart explication -> bulle suivante
W_IN, W_RX, W_LLM, W_OUT = 360, 520, 570, 380
X_IN = 40
X_RX = X_IN + W_IN + 120
X_LLM = X_RX + W_RX + 170
X_OUT = X_LLM + W_LLM + 150
ANN_DX = 240    # explication à droite d'une bulle de 220 px

# ---------------------------------------------------------------- 2. Regex
r1 = box("R1", "Règles regex", X_RX, TOP, BLUE, w=200)
a = ann("aR1", "Chaque libellé est comparé à environ 270 expressions régulières, écrites à partir de la liste annotée transmise par la DRTI. Les règles qui ont décidé sont notées dans la colonne regex_rules.",
        X_RX + 220, TOP, 36)
d = box("R2", "Une règle\nconclut ?", 0, max(bottom(r1), bottom(a)) + 40, LBLUE, shape="diamond")
move(d, x=X_RX + 100 - d["width"] / 2)
r3 = box("R3", "Oui : code trouvé", X_RX, bottom(d) + 50, GREEN, w=200)
r4 = box("R4", "Non : au LLM", X_RX + 300, 0, RED, w=200)
move(r4, y=cy(d) - r4["height"] / 2)
a3 = ann("aR3", "Y compris le code 00 quand le libellé n'est pas un magasin (« ILLISIBLE », « VIREMENT »…). Le LLM n'est pas appelé.",
         X_RX, bottom(r3) + G, 26)
a4 = ann("aR4", "Aucune règle ne reconnaît le libellé, ou plusieurs règles proposent des codes différents.",
         r4["x"], bottom(r4) + G, 26)
arrow("R1R2", "R1", "b", "R2", "t")
arrow("R2R3", "R2", "b", "R3", "t")
arrow("R2R4", "R2", "r", "R4", "l")
rx_bottom = max(bottom(a3), bottom(a4))

# ---------------------------------------------------------------- 3. LLM
def llm_step(id_, label, bg, text, y, below=False):
    b = box(id_, label, X_LLM, y, bg, w=230)
    if below:   # explication sous la bulle (quand des flèches partent à droite)
        t = ann("a" + id_, text, X_LLM, bottom(b) + G, 60)
    else:
        t = ann("a" + id_, text, X_LLM + max(ANN_DX, b["width"] + 20), y, 38)
    return b, t


def nxt(*es):
    return max(bottom(e) for e in es) + STEP


l1, t1 = llm_step("L1", "Couples à classer", LVIOLET,
                  "Une même enseigne dans une même commune n'est envoyée qu'une fois. Plusieurs couples sont traités en parallèle.", TOP)
l2, t2 = llm_step("L2", "Déjà en cache ?", LVIOLET,
                  "Les couples classés lors d'un run précédent sont repris tels quels, sans nouvel appel au modèle.", nxt(l1, t1))
l5, t5 = llm_step("L5", "Prompt", LVIOLET,
                  "Le prompt donne au modèle la nomenclature et ses exemples annotés, le libellé et la commune. Il demande un code, une justification et une confiance (haute, moyenne, basse) ; 99 si l'enseigne n'est que probable.", nxt(l2, t2))
l6 = box("L6", "Modèle llm.lab", X_LLM, nxt(l5, t5), VIOLET, w=230)
l7 = box("L7", "Recherche web", X_LLM + W_LLM - 220, l6["y"], BLUE, w=220)
t6 = ann("aL6", "Par défaut, une première recherche web (SearXNG ou Serper) est imposée au modèle ; avec --no-force-search, il décide seul s'il cherche. Il lit les résultats et peut recommencer, 5 fois au plus.",
         X_LLM + ANN_DX, bottom(l6) + G, 38)
l8, t8 = llm_step("L8", "Vérification du code", LVIOLET,
                  "Le code proposé doit exister dans la nomenclature. Sinon la ligne est marquée en erreur et sera retentée au prochain run. La confiance va dans la colonne llm_confidence : les lignes « basse » sont à relire.",
                  nxt(l6, t6), below=True)
arrow("L1L2", "L1", "b", "L2", "t")
arrow("L2L5", "L2", "b", "L5", "t", label="non")
arrow("L5L6", "L5", "b", "L6", "t")
arrow("L6L7", "L6", "r", "L7", "l", shift=(0, -10, 0, -10))
arrow("L7L6", "L7", "l", "L6", "r", shift=(0, 10, 0, 10))
arrow("L6L8", "L6", "b", "L8", "t")
cx = X_RX + W_RX + 60   # couloir regex -> LLM
arrow("R4L1", "R4", "r", "L1", "l", via=[(cx, cy(r4)), (cx, cy(l1))])

# ---------------------------------------------------------------- 1. Inputs
i1 = box("I1", "Liste d'enseignes", X_IN + (W_IN - 240) / 2, TOP, GRAY)
move(i1, y=cy(r1) - i1["height"] / 2)
ai1 = ann("aI1", "Un fichier CSV/TSV, ou Parquet (local ou sur le S3, colonnes MAG_DEP et COM_DEP) : une ligne par magasin, avec son libellé et sa commune.",
          X_IN, bottom(i1) + G, 44)
i2 = box("I2", "Nomenclature", X_IN + (W_IN - 240) / 2, rx_bottom + 60, GRAY)
ai2 = ann("aI2", "nmcl_magasins_exemples.csv (S3) : les 76 codes, la plupart avec jusqu'à 10 exemples tirés de la liste annotée. Elle est donnée au modèle dans le prompt, puis sert à vérifier sa réponse.",
          X_IN, bottom(i2) + G, 44)
i3 = box("I3", ".env", X_IN + (W_IN - 240) / 2, bottom(ai2) + STEP, GRAY)
ai3 = ann("aI3", "Les réglages : clé d'API llm.lab, nom du modèle, moteur de recherche web utilisé.",
          X_IN, bottom(i3) + G, 44)
arrow("I1R1", "I1", "r", "R1", "l")
cx2 = cx + 30
arrow("I2L5", "I2", "r", "L5", "l", dashed=True, via=[(cx2, cy(i2)), (cx2, cy(l5))], label_seg=0)
cx3 = cx + 60
arrow("I3L6", "I3", "r", "L6", "l", dashed=True, via=[(cx3, cy(i3)), (cx3, cy(l6))], label_seg=0)

# ---------------------------------------------------------------- 4. Outputs
o1 = box("O1", "Cache\n(.cache.jsonl)", X_OUT + (W_OUT - 240) / 2, 0, YELLOW)
move(o1, y=cy(l2) - o1["height"] / 2)
ao1 = ann("aO1", "Chaque réponse réussie y est ajoutée dès qu'elle arrive. Relancer la même commande (un nouveau run) reprend là où le précédent s'était arrêté. Avec --no-cache, le cache n'est ni lu ni écrit : tout est reclassé (évaluation).",
          X_OUT, bottom(o1) + G, 46)
o2 = box("O2", "Fichier codé\n(_coded.csv / .parquet)", X_OUT + (W_OUT - 240) / 2, 0, GREEN)
move(o2, y=max(cy(l8) - o2["height"] / 2, bottom(ao1) + STEP))
ao2 = ann("aO2", "Réécrit en entier à chaque run : il contient tout ce qui a été codé jusque-là. Pour un Parquet, il est écrit sur le S3 dans runs/run_JJMMAAAA_HHMM_SS/. Juste avant l'écriture, un contrôle de cohérence regroupe les libellés équivalents (SAS, pluriel, article : « LES VERGERS DE VENDEE » = « VERGER DE VENDEE ») et signale ceux qui ont des codes différents (colonne coherence_codes). Voir l'exemple en bas.",
          X_OUT, bottom(o2) + G, 46)
gx = X_LLM + W_LLM + 60   # couloir LLM -> sorties
arrow("L8O2", "L8", "r", "O2", "l", via=[(gx + 30, cy(l8)), (gx + 30, cy(o2))] if abs(cy(l8) - cy(o2)) > 2 else None)
arrow("L8O1", "L8", "r", "O1", "l", via=[(gx, cy(l8) - 12), (gx, cy(o1))], shift=(0, -12, 0, 0))
ymid = l2["y"] - 28
arrow("O1L2", "O1", "t", "L2", "t", label="relu au run suivant", dashed=True, shift=(0, 0, 60, 0),
      via=[(o1["x"] + o1["width"] / 2, ymid), (l2["x"] + l2["width"] / 2 + 60, ymid)], label_seg=1)

content_bottom = max(bottom(e) for e in elements if e["type"] != "arrow")
bus = content_bottom + 50
arrow("R3O2", "R3", "l", "O2", "r", label="code posé par la regex : directement dans le fichier codé",
      via=[(X_RX - 12, cy(r3)), (X_RX - 12, bus), (X_OUT + W_OUT + 40, bus), (X_OUT + W_OUT + 40, cy(o2))], label_seg=2)

# ---------------------------------------------------------------- 5. Exemple de sortie
ty = bus + 170
cols = [("libelle", 220, False), ("city", 230, False), ("code", 70, True), ("store_type", 330, True),
        ("method", 95, True), ("regex_rules", 130, True), ("llm_websearch", 135, True), ("llm_confidence", 140, True), ("llm_justification", 330, True)]
rows = [
    ["SUPER U", "ERQUY", "112", "Supermarchés, magasins multi-commerces", "regex", "112-01A", "", "", ""],
    ["ELEPHANT BLEU", "COUDEKERQUE-BRANCHE", "641", "Service d'entretien, de réparation…", "regex", "641-03A", "", "", ""],
    ["A KABOUL", "ALENCON", "411", "Restauration classique", "llm", "", "true", "haute", "A KABOUL est un restaurant…"],
    ["MK2", "PARIS", "622", "Lieux récréatifs et sportifs (parc…", "llm", "", "false", "basse", "MK2 est une célèbre chaîne de cinémas…"],
    ["ILLISIBLE", "", "00", "Hors champ (pas une enseigne)", "regex", "00-01A", "", "", ""],
    ["NOUVELLE ENSEIGNE", "LYON", "", "", "pending", "", "", "", ""],
]
CELL_H, CELL_FS = 34, 14
tx0 = X_IN
table_w = sum(w for _, w, _ in cols)
# accolades : colonnes d'entrée / colonnes ajoutées
w_in = sum(w for _, w, added in cols if not added)
for key, x, w, label, color in [("in", tx0, w_in, "colonnes du fichier d'entrée", ANN_COLOR),
                                ("add", tx0 + w_in, table_w - w_in, "colonnes ajoutées par run.py (extrait : il y en a d'autres)", "#1e7a3c")]:
    base(f"BR_{key}", "line", x + 4, ty - 8, w - 8, 0, strokeColor=color, strokeWidth=2, points=[[0, 0], [w - 8, 0]],
         lastCommittedPoint=None, startArrowhead=None, endArrowhead=None)
    text_el(f"BRT_{key}", label, x + 6, ty - 32, 14, align="left", color=color)


def cell(id_, text, x, y, w, bg, color=TXT, family=2):
    r = base(id_, "rectangle", x, y, w, CELL_H, backgroundColor=bg, strokeColor="#9a9a9a")
    if not text:  # cellule vide : pas d'élément texte
        return
    tw, th = tsize(text or " ", CELL_FS)
    assert tw <= w - 10, (text, tw, w)
    t = text_el(id_ + "_t", text, x + 8, y + (CELL_H - th) / 2, CELL_FS, container=id_, align="left", color=color, family=family)
    r["boundElements"].append({"type": "text", "id": t["id"]})


x = tx0
for j, (name, w, added) in enumerate(cols):
    cell(f"H{j}", name, x, ty, w, GREEN if added else GRAY, family=3)
    for i, row in enumerate(rows):
        cell(f"C{i}_{j}", row[j], x, ty + CELL_H * (i + 1), w, "#ffffff" if i < 4 else "#f6f6f6",
             color=TXT if i < 4 else "#6a6a6a")
    x += w
table_bottom = ty + CELL_H * (len(rows) + 1)
atab = ann("aTAB", "Lignes 1 à 4 : extrait réel de data/shops_coded.csv ; sa colonne llm_confidence est illustrative (extrait antérieur à ce champ). Lignes 5 et 6 (en gris) : exemples fictifs d'un libellé hors champ et d'une ligne pas encore traitée. "
    "« method » dit qui a posé le code : regex, llm, error (échec du LLM, retenté au prochain run) ou pending (pas encore traité, par exemple avec --limit ou --no-llm).\n"
    "« regex_rules » donne les règles regex qui ont décidé, au format code-numéro-tier : 112-01A = 1re règle du code 112, "
    "tier A (déduite de la liste annotée ; B = extension à relire). Vide si aucune règle ne reconnaît le libellé ; "
    "pour un libellé ambigu, elle liste les règles en conflit (ex. 413-04B,652-02A) et regex_candidate_codes les codes concernés.\n"
    "« llm_confidence » : confiance déclarée par le modèle (haute, moyenne, basse). Pour relire en priorité : les lignes « basse » et les codes 99 "
    "(ex. MK2, classé sans recherche : un cinéma relève de 621, pas de 622), puis les lignes où coherence_codes n'est pas vide.",
    tx0, table_bottom + 14, 150)
# une ligne du cache
cx0 = tx0 + table_w + 60
text_el("CACHE_T", "Une ligne du cache (.cache.jsonl)", cx0, ty - 32, 14, align="left", color="#8a6d00")
cache_txt = ('{"key": ["A KABOUL", "ALENCON"],\n "result": {\n   "code": "411",\n   "confidence": "haute",\n   "justification": "A KABOUL est un\n      restaurant situé à Alençon…",\n'
             '   "searches": [ …1 recherche web… ],\n   "tokens": 4055,\n   "error": null }}')
tw, th = tsize(cache_txt, 13)
cb = base("CACHE", "rectangle", cx0, ty, tw + 30, th + 24, backgroundColor=YELLOW, strokeColor="#9a9a9a")
ct = text_el("CACHE_tx", cache_txt, cx0 + 15, ty + 12, 13, container="CACHE", align="left", family=3)
cb["boundElements"].append({"type": "text", "id": ct["id"]})
doc_bottom = max(bottom(atab) + 10, ty + th + 24)

# ---------------------------------------------------------------- Cadres, titres
frames = []
HB = 40  # hauteur des bandeaux de titre
for key, title, x, w, y0, y1 in [("in", "1. Entrées", X_IN, W_IN, TOP - 75, bus - 25),
                                 ("rx", "2. Règles regex", X_RX, W_RX, TOP - 75, bus - 25),
                                 ("llm", "3. LLM + recherche web", X_LLM, W_LLM, TOP - 75, bus - 25),
                                 ("out", "4. Sorties", X_OUT, W_OUT, TOP - 75, bus - 25),
                                 ("ex", "5. Ce qui sort, concrètement : le fichier codé et le cache", X_IN, X_OUT + W_OUT - X_IN, bus + 85, doc_bottom + 20)]:
    if key == "ex":
        frames.append(base(f"F_{key}", "rectangle", x - 25, y0 - 45, w + 50, y1 - y0 + 45, strokeColor="#cccccc"))
        frames.append(base(f"B_{key}", "rectangle", x - 25, y0 - 45, w + 50, HB, strokeColor=NAVY, backgroundColor=NAVY))
        frames.append(text_el(f"T_{key}", title, x, y0 - 45 + (HB - 20 * LH) / 2, 20, align="left", color="#ffffff"))
        continue
    frames.append(base(f"F_{key}", "rectangle", x - 25, y0, w + 50, y1 - y0, strokeColor="#cccccc"))
    frames.append(base(f"B_{key}", "rectangle", x - 25, y0, w + 50, HB, strokeColor=NAVY, backgroundColor=NAVY))
    frames.append(text_el(f"T_{key}", title, x, y0 + (HB - 20 * LH) / 2, 20, align="left", color="#ffffff"))
frames.append(text_el("TITLE", "Codage des enseignes dans la nomenclature de magasins : fonctionnement de run.py", 40, 24, 28, align="left", color=NAVY))
frames.append(text_el("NOTE", "Un run = une exécution de « uv run run.py <fichier> ». Flèches pleines : le chemin d'une ligne du fichier. Pointillés : ce qui est lu en plus (nomenclature, réglages, cache).",
                      40, 66, 15, align="left", color=ANN_COLOR))
for e in frames:
    elements.remove(e)
elements[:0] = frames

# ---------------------------------------------------------------- Contrôles
ids = {e["id"] for e in elements}
for e in elements:
    if e["type"] == "arrow":
        assert e["startBinding"]["elementId"] in ids and e["endBinding"]["elementId"] in ids
    if e["type"] == "text" and e["containerId"] and by_id[e["containerId"]]["type"] != "arrow":
        c = by_id[e["containerId"]]
        assert e["width"] <= c["width"] + 1, (e["id"], e["width"], c["width"])
solid = [e for e in elements if e["type"] in ("rectangle", "diamond", "text") and not e["id"].startswith(("F_", "T_", "B_", "TITLE", "NOTE", "H", "C", "BR", "aTAB"))
         and not (e["type"] == "text" and e["containerId"])]
for i, p in enumerate(solid):
    for q in solid[i + 1:]:
        if p["x"] < q["x"] + q["width"] and q["x"] < p["x"] + p["width"] and p["y"] < q["y"] + q["height"] and q["y"] < p["y"] + p["height"]:
            sys.exit(f"chevauchement {p['id']} / {q['id']}")
for key, x, w in [("in", X_IN, W_IN), ("rx", X_RX, W_RX), ("llm", X_LLM, W_LLM), ("out", X_OUT, W_OUT)]:
    for e in solid:
        if x - 25 <= e["x"] < x + w + 25:
            assert e["x"] + e["width"] <= x + w + 10, ("déborde de sa colonne", e["id"])

free = [e for e in solid if e["type"] == "text"]
for e in elements:
    if e["type"] != "arrow":
        continue
    P = [(e["x"] + px, e["y"] + py) for px, py in e["points"]]
    for (ax, ay), (bx, by) in zip(P, P[1:]):
        for k in range(1, 60):
            px, py = ax + (bx - ax) * k / 60, ay + (by - ay) * k / 60
            for t in free:
                if t["x"] - 4 < px < t["x"] + t["width"] + 4 and t["y"] - 4 < py < t["y"] + t["height"] + 4:
                    sys.exit(f"la flèche {e['id']} traverse le texte {t['id']}")
doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com", "elements": elements,
       "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"}, "files": {}}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False, indent=1)
print(len(elements), "éléments ; bas", bus)
