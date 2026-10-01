"""Marketing inputs: facts that neither the BDX feed nor Blueprint carries.

TEMPORARY HOME (2026-10-01): Blake wants these maintained here for now and moved
upstream later (flagged as a future fix). Keep the field list in one place so the
move is mechanical.

Values are plain text. "lines" fields hold one item per line (feature bullets,
utility providers, attractions) and render as lists.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db
from .auth import now


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"   # text | lines | para | photo | photos
    hint: str = ""


COMMUNITY_FIELDS = [
    Field("description", "Flyer description", "para",
          "Optional. Replaces the feed's community description on the Community Info sheet. "
          "One paragraph per line. Blank = use the feed's copy."),
    Field("hero_photo", "Hero photo", "photo", "Pick from the community's photos in the feed. None = the feed's preferred photo."),
    Field("amenity_photos", "Amenity photos", "photos", "Pick up to 3 for the Community Info sheet. None = the feed's next three."),
    Field("lot_size", "Lot size", hint="e.g. Standard, 50' lots, Half acre"),
    Field("hoa", "HOA", hint="Shown only when Blueprint has no HOA for this community, e.g. $900/year"),
    Field("tax_rate", "Tax rate", hint="Shown only when Blueprint has no tax rate, e.g. 1.909%"),
    Field("utilities", "Utility providers", "lines", "One per line, e.g. Water & Sewer: Denton"),
    Field("attractions", "Attractions", "lines", "One per line"),
]
PLAN_FIELDS = [
    Field("beds_range", "Beds range", hint="e.g. 3 - 4 (blank = the feed's base value)"),
    Field("baths_range", "Baths range", hint="e.g. 2.5 - 3.5 (blank = the feed's base value)"),
]
HOME_FIELDS = [
    Field("features", "Feature bullets", "lines", "One per line, up to 8"),
]
FIELDS = {"community": COMMUNITY_FIELDS, "plan": PLAN_FIELDS, "home": HOME_FIELDS}


def community_subject(c) -> str:
    return c.number or c.slug


def plan_subject(c, plan_name: str) -> str:
    return f"{community_subject(c)}|{plan_name}"


class Inputs:
    """All current values, loaded once per request: inputs.get(scope, subject, field)."""

    def __init__(self, rows: list[dict]):
        self._v = {(r["scope"], r["subject"], r["field"]): r["value"] for r in rows}

    def get(self, scope: str, subject: str, field: str) -> str | None:
        v = self._v.get((scope, subject, field))
        return v if v and v.strip() else None

    def lines(self, scope: str, subject: str, field: str) -> list[str]:
        v = self.get(scope, subject, field) or ""
        return [ln.strip() for ln in v.splitlines() if ln.strip()]


def load() -> Inputs:
    with db.connect() as c:
        return Inputs(c.q("SELECT scope, subject, field, value FROM inputs"))


def save(scope: str, subject: str, values: dict[str, str], user_id: int) -> int:
    """Upsert the given fields; blank clears. Returns the number of fields that changed."""
    allowed = {f.key: f for f in FIELDS[scope]}
    changed, ts = 0, now()
    with db.connect() as c:
        for key, raw in values.items():
            if key not in allowed:
                continue
            f = allowed[key]
            if f.kind in ("lines", "para", "photos"):
                value = "\n".join(ln.strip() for ln in (raw or "").splitlines() if ln.strip())
            else:
                value = " ".join((raw or "").split())
            cur = c.one("SELECT value FROM inputs WHERE scope = ? AND subject = ? AND field = ?",
                        (scope, subject, key))
            if (cur["value"] if cur else "") == value:
                continue
            if value:
                if cur:
                    c.x("UPDATE inputs SET value = ?, updated_by = ?, updated_at = ? "
                        "WHERE scope = ? AND subject = ? AND field = ?", (value, user_id, ts, scope, subject, key))
                else:
                    c.x("INSERT INTO inputs (scope, subject, field, value, updated_by, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (scope, subject, key, value, user_id, ts))
            else:
                c.x("DELETE FROM inputs WHERE scope = ? AND subject = ? AND field = ?", (scope, subject, key))
            c.x("INSERT INTO input_history (scope, subject, field, value, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (scope, subject, key, value or None, user_id, ts))
            changed += 1
    return changed


def last_edit(scope: str, subjects: list[str]) -> dict | None:
    """Most recent change among the given subjects, with the editor's name."""
    if not subjects:
        return None
    marks = ",".join("?" for _ in subjects)
    with db.connect() as c:
        return c.one(f"SELECT h.updated_at, u.name FROM input_history h LEFT JOIN users u ON u.id = h.updated_by "
                     f"WHERE h.scope = ? AND h.subject IN ({marks}) ORDER BY h.id DESC LIMIT 1",
                     (scope, *subjects))


def community_photos(c, vals: "Inputs") -> tuple[str | None, list[str]]:
    """Hero and up to three amenity photos: marketing's picks when they're still in the
    feed, otherwise the feed's preferred photo and the next three."""
    cs = community_subject(c)
    feed = list(c.photos)
    hero = vals.get("community", cs, "hero_photo")
    hero = hero if hero in feed else c.hero
    picks = [u for u in vals.lines("community", cs, "amenity_photos") if u in feed][:3]
    if not picks:
        picks = [u for u in feed if u != hero][:3]
    return hero, picks


def description(c, vals: "Inputs") -> list[str]:
    """Flyer description paragraphs: marketing's override, else the feed's copy."""
    over = vals.lines("community", community_subject(c), "description")
    return over or [p for p in (c.description or "").split("\n") if p.strip()]
