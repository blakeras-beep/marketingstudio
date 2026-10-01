"""One-time import of today's published flyer facts into empty marketing inputs.

app/seed/reference_inputs.json is built from reference/flyers by scripts/build_seed.py.
Importing only fills fields that are empty in Studio, so it never overwrites marketing's work,
and it can be run again safely after the reference folder or the feed changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db, bdx, inputs

SEED = Path(__file__).parent / "seed" / "reference_inputs.json"
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
STREET = {"ln": "lane", "dr": "drive", "ct": "court", "rd": "road", "st": "street", "ave": "avenue",
          "blvd": "boulevard", "pl": "place", "cir": "circle", "trl": "trail", "pkwy": "parkway"}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def plan_key(name: str) -> str:
    """'Brookstone I' / 'BROOKSTONE 1' -> 'brookstone1'."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return "".join(ROMAN.get(w, w) for w in words)


def address_key(addr: str) -> str:
    words = re.findall(r"[a-z0-9]+", (addr or "").lower())
    return "".join(STREET.get(w, w) for w in words)


def _community_for(seed_name: str, communities: list) -> "bdx.Community | None":
    k = _key(seed_name)
    exact = [c for c in communities if _key(c.name) == k]
    if exact:
        return exact[0]
    # folder names carry phase suffixes: "Mayfield Farms 4", "Sheppard's Place - Ph 3"
    pref = sorted((c for c in communities if k.startswith(_key(c.name))), key=lambda c: -len(c.name))
    return pref[0] if pref else None


def run(user_id: int) -> dict:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    communities = bdx.state().communities
    vals = inputs.load()
    out = {"fields": 0, "communities": 0, "plans": 0, "homes": 0, "unmatched": []}

    dated = data.get("dated", {})

    def untouched_import(scope, subject, key, stamp) -> bool:
        """The current value is still the one an import wrote: it carries the import's stamp
        (the sheet's date at 12:00 UTC), which an edit made in the Studio never does."""
        with db.connect() as conn:
            row = conn.one("SELECT updated_at FROM inputs WHERE scope = ? AND subject = ? AND field = ?",
                           (scope, subject, key))
        return bool(row) and row["updated_at"] == stamp

    def fill(scope, subject, values: dict, folder: str | None = None) -> int:
        """Empty fields only. A field taken from a dated sheet (features, elevation prices) is
        stamped with that sheet's date, so "effective" on the flyer is the original's date; such a
        field is also refreshed when nobody has edited it since it was imported (a better read of
        the same sheet), never when marketing has."""
        when = dated.get(folder or "", {})
        n = 0
        for key in ("standard_features", "elevations"):
            new = values.get(key)
            if not new or not when.get(key):
                continue
            stamp = when[key] + "T12:00:00+00:00"
            if not vals.get(scope, subject, key) or untouched_import(scope, subject, key, stamp):
                n += inputs.save(scope, subject, {key: new}, user_id, at=stamp)
            values = {k: v for k, v in values.items() if k != key}
        empty = {k: v for k, v in values.items() if v and not vals.get(scope, subject, k)}
        return n + (inputs.save(scope, subject, empty, user_id) if empty else 0)

    def as_text(v):
        return "\n".join(v) if isinstance(v, list) else v

    for name, rec in data.get("communities", {}).items():
        c = _community_for(name, communities)
        if c is None:
            out["unmatched"].append(f"community {name}")
            continue
        n = fill("community", inputs.community_subject(c), {k: as_text(v) for k, v in rec.items()}, name)
        out["fields"] += n
        out["communities"] += bool(n)

    for name, plans in data.get("plans", {}).items():
        c = _community_for(name, communities)
        if c is None:
            continue
        by_key = {plan_key(p.name): p for p in c.plans}
        for printed, rec in plans.items():
            p = by_key.get(plan_key(printed))
            if p is None:
                out["unmatched"].append(f"plan {printed} ({c.name})")
                continue
            n = fill("plan", inputs.plan_subject(c, p.name or ""), rec, name)
            out["fields"] += n
            out["plans"] += bool(n)

    # Elevation captions matched by image from the plan flyers: one line per feed image, in feed
    # order, "-" where no rendering matched confidently. Applies to the plan in every community.
    labels = data.get("elevation_labels", {})
    for c in communities:
        for p in c.plans:
            by_url = labels.get(plan_key(p.name or ""))
            if not by_url or not any(u in by_url for u in p.elevations):
                continue
            n = fill("plan", inputs.plan_subject(c, p.name or ""),
                     {"elevation_labels": "\n".join(by_url.get(u, "-") for u in p.elevations)})
            out["fields"] += n

    for name, homes in data.get("homes", {}).items():
        c = _community_for(name, communities)
        if c is None:
            continue
        by_addr = {address_key(h.address or ""): h for h in c.homes}
        for addr, rec in homes.items():
            h = by_addr.get(address_key(addr))
            if h is None:   # sold since the flyer was made: nothing to fill
                continue
            n = fill("home", h.id, {k: as_text(v) for k, v in rec.items()})
            out["fields"] += n
            out["homes"] += bool(n)
    return out
