"""Display formatting. Every helper prints an em dash for a missing value."""
from __future__ import annotations

import re
from datetime import date

DASH = "—"
READY = "Ready Now"


def money(v) -> str:
    return f"${v:,.0f}" if v else DASH


def num(v) -> str:
    if not v:
        return DASH
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def sqft(v) -> str:
    return f"{v:,}" if v else DASH


def baths(full, half=None) -> str:
    """2 full + 1 half -> '2.5'; matches how Sandlin collateral prints baths."""
    if not full:
        return DASH
    total = float(full) + 0.5 * float(half or 0)
    return f"{total:g}"


def text(v) -> str:
    return v if v else DASH


def move_in(d: date | None, today: date | None = None) -> str:
    if not d:
        return DASH
    today = today or date.today()
    if d <= today:
        return READY
    return d.strftime("%b %Y")


def plan_label(name) -> str:
    """'The Cedarwood Plan' — without doubling a name that already ends in 'Plan'."""
    if not name:
        return DASH
    return f"The {name}" if name.lower().endswith("plan") else f"The {name} Plan"


def city_line(city, state, zip_=None) -> str:
    left = ", ".join(x for x in (city, state) if x)
    return " ".join(x for x in (left, zip_) if x) or DASH


def home_specs(h) -> str:
    """'4 Beds · 3.5 Baths · 2,684 Sq. Ft. · 2 Story · 3 Car Garage' — omits what's missing."""
    parts = []
    if h.beds:
        parts.append(f"{num(h.beds)} Beds")
    if h.baths:
        parts.append(f"{baths(h.baths, h.half_baths)} Baths")
    if h.sqft:
        parts.append(f"{sqft(h.sqft)} Sq. Ft.")
    if h.stories:
        parts.append(f"{num(h.stories)} Story")
    if h.garage:
        parts.append(f"{num(h.garage)} Car Garage")
    return " · ".join(parts) or DASH


def sqft_range(r) -> str:
    if not r:
        return DASH
    lo, hi = r
    return f"{lo:,} Sq. Ft." if lo == hi else f"{lo:,} – {hi:,} Sq. Ft."


def status(s) -> str:
    """BDX status 'GrandOpening' -> 'Grand Opening'."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s) if s else DASH


def phone_paren(p) -> str:
    """'972-723-8778' -> '(972) 723-8778' (the Excel sheets' style)."""
    d = re.sub(r"\D", "", p or "")
    return f"({d[:3]}) {d[3:6]}-{d[6:10]}" if len(d) >= 10 else (p or DASH)


def range_or(rng, value_fmt) -> str:
    """A marketing-entered range ('3 - 4') if there is one, else the feed's single value."""
    return rng if rng else value_fmt


def isd(name) -> str:
    """'Denton Independent School District' -> 'Denton ISD' (how the flyers print it)."""
    return re.sub(r"\s*Independent School District\b", " ISD", name or "").strip() or DASH


FILTERS = {
    "isd": isd,
    "phone_paren": phone_paren,
    "status": status,
    "money": money,
    "num": num,
    "sqft": sqft,
    "baths": baths,
    "text": text,
    "move_in": move_in,
    "home_specs": home_specs,
    "sqft_range": sqft_range,
}
