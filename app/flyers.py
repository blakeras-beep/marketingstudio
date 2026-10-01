"""The flyer catalog: what each community offers and how long lists paginate.

Repository model: every flyer that a community's live data supports is listed
and rendered on demand. Nothing is generated ahead of time, so a flyer
opened next month prints next month's prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import inputs
from .bdx import Community, Home, Plan


@dataclass(frozen=True)
class Flyer:
    key: str
    title: str
    blurb: str
    template: str
    needs: Callable[[Community, "inputs.Inputs"], str | None]  # reason it can't render, or None


def _needs_homes(c: Community, vals) -> str | None:
    return None if c.homes else "No homes in the feed"


def _needs_plans(c: Community, vals) -> str | None:
    return None if c.plans else "No plans in the feed"


def elevation_rows(c: Community, vals) -> list[dict]:
    """All Elevations rows: marketing-maintained elevations for the community's current plans."""
    rows = []
    for p in sorted(c.plans, key=lambda p: (p.name or "").lower()):
        for e in inputs.parse_elevations(vals.get("plan", inputs.plan_subject(c, p.name or ""), "elevations")):
            rows.append({"plan": p.name, **e})
    return rows


def _needs_elevations(c: Community, vals) -> str | None:
    return None if elevation_rows(c, vals) else "Marketing hasn't entered elevation prices yet"


def _needs_features(c: Community, vals) -> str | None:
    have = vals.get("community", inputs.community_subject(c), "standard_features")
    return None if have else "Marketing hasn't entered standard features yet"


COMMUNITY_FLYERS: list[Flyer] = [
    Flyer("info", "Community Info Sheet",
          "Overview, schools, price range and sales office.",
          "flyers/info.html", lambda c, v: None),
    Flyer("pricing", "Inventory List",
          "Every available home with plan, specs, price and move-in.",
          "flyers/pricing.html", _needs_homes),
    Flyer("plans", "Price Sheet",
          "Every plan with specs and starting price.",
          "flyers/plans.html", _needs_plans),
    Flyer("grid", "Photo Inventory",
          "Photo cards for every available home.",
          "flyers/grid.html", _needs_homes),
    Flyer("elevations", "All Elevations",
          "Every plan and elevation with living square feet and price.",
          "flyers/elevations.html", _needs_elevations),
    Flyer("features", "Standard Features",
          "Included features by area of the home.",
          "flyers/features.html", _needs_features),
]
FLYERS_BY_KEY = {f.key: f for f in COMMUNITY_FLYERS}

HOME_FLYER = Flyer("home", "Inventory Flyer",
                   "Two pages: photo and facts, then floor plan.",
                   "flyers/home.html", lambda c, v: None)
PLAN_FLYER = Flyer("plan", "Plan Flyer",
                   "Two pages: elevations, then floor plan.",
                   "flyers/plan.html", lambda c, v: None)

# Rows per printed page. The first page carries the community header, so it
# holds fewer. Rows are single-line and fixed-height, which keeps these exact;
# the page template flags any sheet that still overflows.
PRICING_ROWS = (15, 15)   # inventory list: rows the Excel original fits above its footer
PLAN_ROWS = (12, 12)      # price sheet
GRID_CARDS = (10, 10)     # photo inventory: 2 columns x 5 rows
ELEVATION_ROWS = (44, 44)  # all elevations


def paginate(rows: list, first: int, rest: int) -> list[list]:
    pages = [rows[:first]]
    rows = rows[first:]
    while rows:
        pages.append(rows[:rest])
        rows = rows[rest:]
    return pages


def _price_key(v):
    return (v is None, v or 0)


def sorted_homes(c: Community) -> list[Home]:
    return sorted(c.homes, key=lambda h: (_price_key(h.price), h.address or h.id))


def sorted_plans(c: Community) -> list[Plan]:
    """Cheapest first, like the price sheets (plans without a price last, by size)."""
    return sorted(c.plans, key=lambda p: (_price_key(p.price_from), p.sqft or 0, p.name or ""))
