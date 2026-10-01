"""Build app/seed/reference_inputs.json from the current flyers in reference/flyers.

Reads the published PDFs (Canva, Word, Excel exports) and pulls out the marketing facts
the BDX feed doesn't carry, so Studio's inputs can start from what Sandlin publishes today.
Nothing here is invented: every value is text printed on a current flyer. Run after the
reference folder changes:

    python scripts/build_seed.py

Needs PyMuPDF (dev only): pip install pymupdf
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "reference" / "flyers"
OUT = ROOT / "app" / "seed" / "reference_inputs.json"


def spans(page):
    """(x0, y0, x1, y1, text, font, size, color) for every real text span (Canva's Type3 faux-bold
    duplicates are dropped)."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if not s["text"].strip() or s["font"].startswith("Type3"):
                    continue
                x0, y0, x1, y1 = s["bbox"]
                out.append((x0, y0, x1, y1, s["text"], s["font"], round(s["size"], 1), s["color"]))
    return out


def lines_of(sp, tol=2.0):
    """Group spans into visual lines (by bottom y), left to right. Small raised spans
    (superscript ordinals like the 'nd' in 2nd) are grouped with the line they sit on."""
    def bottom(s):
        return s[3] + (5 if s[6] < 9 else 0)
    sp = sorted(sp, key=lambda s: (round(bottom(s)), s[0]))
    lines: list[list] = []
    for s in sp:
        if lines and abs(bottom(lines[-1][0]) - bottom(s)) <= tol:
            lines[-1].append(s)
        else:
            lines.append([s])
    return [sorted(l, key=lambda s: s[0]) for l in lines]


def clean(t: str) -> str:
    return " ".join(t.replace(" ", " ").split())


# ---------------------------------------------------------------- community info (Canva)

HEADINGS = {"community info": None, "schools": None, "utility providers": "utilities",
            "attractions": "attractions", "amenities": "attractions", "nearby amenities": "attractions"}
FACT = re.compile(r"^(HOA|Tax Rate|Property Tax|Lot Size)\s*[:\-]\s*(.+)$", re.I)
FACT_KEY = {"hoa": "hoa", "tax rate": "tax_rate", "property tax": "tax_rate", "lot size": "lot_size"}
CONTINUES = re.compile(r"(\b(to|of|and|at|in|&)|[-,&])$", re.I)   # a list line ending like this wraps
LONE_WORD = {"school", "airport", "park", "center", "lake", "trail", "arlington"}


def _text(line) -> str:
    """Spans of one line joined, with a space where the PDF left a gap between them."""
    out, last = "", None
    for s in sorted(line, key=lambda s: s[0]):
        out += (" " if last is not None and s[0] - last > 1.5 and not out.endswith(" ") else "") + s[4]
        last = s[2]
    return clean(out)


def _description(page) -> list[str]:
    """The left column's body copy, as paragraphs (a paragraph break is a blank line's gap)."""
    sp = [s for s in spans(page) if s[0] < 380 and 430 < s[1] < 725 and s[7] == 0 and s[6] < 12.5]
    paras: list[str] = []
    last_y = None
    for line in lines_of(sp):
        text, y = _text(line), line[0][1]
        if last_y is None or y - last_y > 25:
            paras.append(text)
        else:
            paras[-1] += ("" if paras[-1].endswith("-") else " ") + text
        last_y = y
    return [p for p in paras if len(p) > 40]


def community_info(pdf: Path) -> dict:
    """Facts, lists and description from a Canva Community Info sheet (current and older layout)."""
    page = pymupdf.open(pdf)[0]
    sp = [s for s in spans(page) if s[0] > 385 and s[1] > 340 and s[7] == 0xFFFFFF]
    # current layout: every list item starts at a small round bullet drawn to its left
    bullets = sorted(d["rect"].y0 for d in page.get_drawings() if d["rect"].width < 6 and d["rect"].x0 > 385)
    out: dict = {}
    current, items = None, {}
    for line in lines_of(sp):
        text, y = _text(line), line[0][1]
        fact = FACT.match(text)
        if fact:
            out.setdefault(FACT_KEY[fact.group(1).lower()], fact.group(2).strip().rstrip(",;").strip())
            continue
        if text.lower() in HEADINGS:
            current = text
            items[current] = []
            continue
        if current is None:
            continue
        lst = items[current]
        if bullets:
            starts = any(-6 < (b - y) < 14 for b in bullets)
            wraps = lst and not starts
        else:   # older layout, no bullets: only an obvious wrap joins the line above
            wraps = lst and (CONTINUES.search(lst[-1]) or text.lower() in LONE_WORD or text[:1].islower())
        if wraps:
            lst[-1] = lst[-1] + ("" if lst[-1].endswith("-") and not lst[-1].endswith(" -") else " ") + text
        else:
            lst.append(text)
    for heading, lst in items.items():
        key = HEADINGS[heading.lower()]
        if key and lst and key not in out:
            out[key] = lst
            if key == "attractions" and heading.lower() != "attractions":
                out["attractions_heading"] = heading.title()
    desc = _description(page)
    if desc:
        out["description"] = desc
    return out


def is_community_info(pdf: Path) -> bool:
    try:
        return "Community Info" in pymupdf.open(pdf)[0].get_text()
    except Exception:
        return False


# ---------------------------------------------------------------- standard features (Word)

def standard_features(pdf: Path) -> str:
    """'# SECTION' / '- item' text with **bold** brand names, in reading order (page, column, line)."""
    doc = pymupdf.open(pdf)
    out: list[str] = []
    item: list[str] | None = None

    def flush():
        nonlocal item
        if item is not None:
            text = clean("".join(item)).replace("** **", " ").replace("****", "")
            if text:
                out.append("- " + text)
        item = None

    for pno, page in enumerate(doc):
        if pno:
            flush()
            out.append("===")   # the original starts a new page here
        sp = [s for s in spans(page) if 160 < s[1] < 735]
        for col in (lambda s: s[0] < 316, lambda s: s[0] >= 316):
            for line in lines_of([s for s in sp if col(s)]):
                first = line[0]
                if first[5].startswith("Garamond") or (first[6] >= 13.5 and clean(first[4]).isupper()):
                    flush()
                    out.append("# " + clean("".join(s[4] for s in line)))
                    continue
                for s in line:
                    t = s[4]
                    if s[5].startswith("Symbol") and t.strip() in ("•", ""):
                        flush()
                        item = []
                        continue
                    if item is None:          # text without a bullet: continuation of nothing; skip
                        continue
                    if item and s is first:
                        item.append(" ")      # wrapped line
                    if s[6] < 9 and re.fullmatch(r"(st|nd|rd|th)", t.strip()):
                        while item and item[-1] == " ":
                            item.pop()
                        item.append(t.strip())  # superscript ordinal: 2nd
                    elif "Bold" in s[5]:
                        lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", t).groups()
                        item.append(f"{lead}**{core}**{trail}" if core else t)
                    else:
                        item.append(t)
        flush()
    # collapse a section split across columns/pages (same heading never repeats in the source)
    return "\n".join(out)


# ---------------------------------------------------------------- single-home flyers (Canva)

def home_flyer(pdf: Path) -> tuple[str | None, list[str]]:
    page = pymupdf.open(pdf)[0]
    sp = spans(page)
    addr = next((clean(s[4]) for s in sp if 320 < s[1] < 380 and s[6] >= 24 and "Garamond" in s[5]), None)
    feats = [clean("".join(x[4] for x in line)) for line in
             lines_of([s for s in sp if 520 < s[1] < 712 and s[0] < 300 and 13 <= s[6] <= 16])]
    return addr, [f for f in feats if f]


# ---------------------------------------------------------------- Excel sheets

def table_rows(pdf: Path, y_min: float, y_max: float) -> list[list[tuple]]:
    rows = []
    for page in pymupdf.open(pdf):
        rows += lines_of([s for s in spans(page) if y_min < s[1] < y_max], tol=3)
    return rows


def _cells(row, gap=4.0) -> list[str]:
    """Spans of one table row joined into cells (a cell is spans closer than `gap`)."""
    row = sorted(row, key=lambda s: s[0])
    cells, last_x1 = [], None
    for s in row:
        if last_x1 is not None and s[0] - last_x1 < gap:
            cells[-1] += s[4]
        else:
            cells.append(s[4])
        last_x1 = s[2]
    return [clean(c) for c in cells if clean(c)]


PRICE = re.compile(r"^\$?\d{2,3}(,\d{3})+(\.\d\d)?$")
SQFT = re.compile(r"^\d{1,2},\d{3}$|^\d{3,4}$")
ELEV = re.compile(r"^(?:Elevation\s+)?([A-Z]{1,3}\d?)$")


def elevations(pdf: Path) -> dict[str, list[str]]:
    """{'': ['Plan | A | 2,167 | 469,900', ...]} from an elevation price list, read from the right
    of each row (price, optional living SF, elevation, plan) so both layouts work: the Excel
    'All Elevations' sheet and the MarkSystems export (company, dev code, model, 'Elevation A', price)."""
    rows = []
    for row in table_rows(pdf, 40, 780):
        cells = _cells(row)
        if cells and cells[-1].upper().endswith("N/A"):     # listed, not priced: kept, price left blank
            rest = cells[-1][:-3].strip()
            cells = cells[:-1] + ([rest] if rest else []) + ["N/A"]
        if len(cells) < 3 or not (PRICE.match(cells[-1]) or cells[-1] == "N/A"):
            continue
        price = "" if cells[-1] == "N/A" else cells[-1].lstrip("$")
        if price.endswith(".00"):
            price = price[:-3]
        i = len(cells) - 2
        sqft = ""
        if SQFT.match(cells[i]):
            sqft, i = cells[i], i - 1
        m = ELEV.match(cells[i]) if i >= 1 else None
        if not m:
            continue
        plan = cells[i - 1]
        rows.append(f"{plan} | {m.group(1)} | {sqft} | {price}")
    return {"": rows} if rows else {}


def sheet_date(pdf: Path) -> str | None:
    """'... 5.27.26.pdf' / '..._09.24.26.pdf' / '4.7.25' -> '2026-05-27' (the date in the file name)."""
    m = re.findall(r"(\d{1,2})\.(\d{1,2})\.(\d{2}(?:\d{2})?)(?!\d)", pdf.stem)
    if not m:
        return None
    mo, d, y = (int(v) for v in m[-1])
    y += 2000 if y < 100 else 0
    try:
        from datetime import date
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def price_sheet(pdf: Path) -> dict[str, dict]:
    """{printed plan name: {beds_range, baths_range}} from a Price Sheet (only real ranges)."""
    out: dict[str, dict] = {}
    for row in table_rows(pdf, 225, 580):
        name = clean("".join(s[4] for s in row if s[0] < 150))
        beds = clean("".join(s[4] for s in row if 320 < s[0] < 375))
        baths = clean("".join(s[4] for s in row if 385 < s[0] < 455))
        if not name or name.lower().startswith("for more"):
            continue
        rec = {}
        if "-" in beds:
            rec["beds_range"] = beds
        if "-" in baths:
            rec["baths_range"] = baths
        if rec:
            out[name] = rec
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    seed: dict = {"communities": {}, "plans": {}, "homes": {}, "sources": []}
    for folder in sorted(p for p in REF.iterdir() if p.is_dir() and not p.name.startswith("_")):
        name = folder.name
        com: dict = {}
        for pdf in sorted(p for p in (folder / "Community Info").glob("*.pdf") if is_community_info(p)):
            try:
                com.update({k: v for k, v in community_info(pdf).items() if v})
                seed["sources"].append(str(pdf.relative_to(ROOT)))
            except Exception as e:  # a differently laid-out sheet: skip it, say so
                print(f"skip {pdf.name}: {e}", file=sys.stderr)
        for pdf in sorted((folder / "Standard Features").glob("*.pdf")):
            text = standard_features(pdf)
            if text.count("\n- ") >= 5:
                com["standard_features"] = text
                if sheet_date(pdf):
                    seed.setdefault("dated", {}).setdefault(name, {})["standard_features"] = sheet_date(pdf)
                seed["sources"].append(str(pdf.relative_to(ROOT)))
        if com:
            seed["communities"][name] = com

        homes = {}
        for pdf in sorted((folder / "Inventory Flyers").glob("*_Inventory_*.pdf")):
            if "Photo" in pdf.name or "Payments" in pdf.name:
                continue
            addr, feats = home_flyer(pdf)
            if addr and feats:
                homes[addr] = {"features": feats}
                seed["sources"].append(str(pdf.relative_to(ROOT)))
        if homes:
            seed["homes"][name] = homes

        plans: dict = {}
        for pdf in sorted((folder / "Pricing").glob("*Price Sheet*.pdf")):
            for plan, rec in price_sheet(pdf).items():
                plans.setdefault(plan, {}).update(rec)
            seed["sources"].append(str(pdf.relative_to(ROOT)))
        for pdf in sorted((folder / "Pricing").glob("*Elevation*.pdf"), key=lambda p: sheet_date(p) or ""):
            got = elevations(pdf)
            if got and sheet_date(pdf):
                seed.setdefault("dated", {}).setdefault(name, {})["elevations"] = sheet_date(pdf)
            for _community, rows in got.items():
                for r in rows:
                    plan, rest = r.split(" | ", 1)
                    plans.setdefault(plan, {}).setdefault("elevations", []).append(rest)
            seed["sources"].append(str(pdf.relative_to(ROOT)))
        for rec in plans.values():
            if "elevations" in rec:
                rec["elevations"] = "\n".join(rec["elevations"])
        if plans:
            seed["plans"][name] = plans

    feed = "https://feed.mybuildercloud.com/bdx/26016593903.xml"
    try:
        seed["elevation_labels"] = elevation_labels(feed)
    except Exception as e:   # no network: keep the rest of the seed
        print(f"elevation labels skipped: {e}", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = lambda k: sum(len(v) for v in seed[k].values())
    print(f"communities {len(seed['communities'])}, plans {n('plans')}, homes {n('homes')}, "
          f"labelled elevation images {sum(len(v) for v in seed.get('elevation_labels', {}).values())} "
          f"-> {OUT.relative_to(ROOT)}")
    return 0




# ---------------------------------------------------------------- elevation labels (plan flyers)
# The feed's elevation images rarely carry a caption, and their order isn't guaranteed to be
# A, B, C. The InDesign plan flyers caption each rendering ("Elevation A"), so each feed image
# is matched to a captioned rendering by how it looks; only confident matches are kept.

def _fingerprint(im):
    """Centre of the rendering (crops differ between the feed and InDesign), as edges + colour."""
    from PIL import Image, ImageFilter
    im = im.convert("RGB")
    w, h = im.size
    im = im.crop((int(w * .15), int(h * .25), int(w * .85), int(h * .9)))
    edges = im.convert("L").filter(ImageFilter.FIND_EDGES).resize((24, 16), Image.BOX)
    e = list(edges.get_flattened_data())
    mean = sum(e) / len(e)
    small = im.resize((6, 4), Image.BOX)
    return [x > mean for x in e], list(small.get_flattened_data())


def _distance(a, b) -> float:
    (ba, ca), (bb, cb) = a, b
    ham = sum(x != y for x, y in zip(ba, bb)) / len(ba)
    col = sum(abs(p - q) for x, y in zip(ca, cb) for p, q in zip(x, y)) / (len(ca) * 3 * 255)
    return ham + col


def plan_flyer_captions(pdf: Path) -> list[tuple[str, object]]:
    """[(caption, fingerprint)] for each captioned rendering on page 1."""
    import io
    from PIL import Image
    doc = pymupdf.open(pdf)
    page = doc[0]
    caps = [(s, clean(s[4])) for s in spans(page) if "Italic" in s[5] and clean(s[4]).lower().startswith("elevation")]
    out = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            cx = (r.x0 + r.x1) / 2
            below = [(s, t) for s, t in caps if 0 <= s[1] - r.y1 < 30 and r.x0 - 5 <= (s[0] + s[2]) / 2 <= r.x1 + 5]
            if not below:
                continue
            s, t = min(below, key=lambda st: abs((st[0][0] + st[0][2]) / 2 - cx))
            pix = pymupdf.Pixmap(doc, img[0])
            if pix.n - pix.alpha > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            out.append((t, _fingerprint(Image.open(io.BytesIO(pix.tobytes("png"))))))
    return out


def _assign(urls, prints, caps) -> dict[str, str]:
    """One caption per image (no caption used twice), by the lowest total distance. An image is
    labelled only when its match is clearly better than its next-best caption."""
    from itertools import permutations
    D = [[_distance(fp, cfp) for _, cfp in caps] for fp in prints]
    n, m = len(urls), len(caps)
    if n <= m:
        best = min(permutations(range(m), n), key=lambda p: sum(D[i][j] for i, j in enumerate(p)))
        pairs = list(enumerate(best))
    else:   # more images than captions: give each caption its closest image
        pairs = []
        for j in range(m):
            i = min(range(n), key=lambda i: D[i][j])
            pairs.append((i, j))
    out = {}
    for i, j in pairs:
        others = [D[i][k] for k in range(m) if k != j and caps[k][0] != caps[j][0]]
        if D[i][j] < 0.45 and (not others or min(others) - D[i][j] > 0.03):
            out[urls[i]] = caps[j][0]
    # Same number of images as captions and only one of each left over: they belong together
    # (typically a re-rendered elevation, e.g. a new stone colour).
    left_u = [u for u in urls if u not in out]
    left_c = [c for c, _ in caps if c not in out.values()]
    if n == m and len(left_u) == 1 and len(left_c) == 1:
        out[left_u[0]] = left_c[0]
    return out


def elevation_labels(feed_url: str) -> dict[str, dict[str, str]]:
    """{plan key: {feed image url: 'Elevation A'}} for plans with a plan flyer."""
    import io
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    sys.path.insert(0, str(ROOT))
    from app import bdx
    from app.seed import plan_key

    with urllib.request.urlopen(feed_url, timeout=60) as r:
        communities = bdx.parse(r.read())
    feed_by_plan: dict[str, set[str]] = {}
    for c in communities:
        for p in c.plans:
            feed_by_plan.setdefault(plan_key(p.name), set()).update(p.elevations)

    refs = {}
    for pdf in sorted((REF / "_Plan Flyers").glob("*.pdf")):
        caps = plan_flyer_captions(pdf)
        if caps:
            refs[plan_key(pdf.stem)] = caps

    def fetch(url):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return url, _fingerprint(Image.open(io.BytesIO(r.read())))
        except Exception as e:
            print(f"skip image {url}: {e}", file=sys.stderr)
            return url, None
    urls = sorted({u for k in refs for u in feed_by_plan.get(k, ())})
    with ThreadPoolExecutor(12) as ex:
        prints = dict(ex.map(fetch, urls))

    out: dict[str, dict[str, str]] = {}
    for k, caps in refs.items():
        urls = [u for u in sorted(feed_by_plan.get(k, ())) if prints.get(u) is not None]
        if urls:
            out[k] = _assign(urls, [prints[u] for u in urls], caps)
    return out


if __name__ == "__main__":
    sys.exit(main())
