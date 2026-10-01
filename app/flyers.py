"""The flyer catalog: what each community offers and how long lists paginate.

Repository model: every flyer that a community's live data supports is listed
and rendered on demand. Nothing is generated ahead of time, so a flyer
opened next month prints next month's prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .bdx import Community, Home, Plan


@dataclass(frozen=True)
class Flyer:
    key: str
    title: str
    blurb: str
    template: str
    needs: Callable[[Community], str | None]  # returns the reason it can't render, or None


def _needs_homes(c: Community) -> str | None:
    return None if c.homes else "No homes in the feed"


def _needs_plans(c: Community) -> str | None:
    return None if c.plans else "No plans in the feed"


COMMUNITY_FLYERS: list[Flyer] = [
    Flyer("info", "Community Info Sheet",
          "Overview, schools, price range and sales office.",
          "flyers/info.html", lambda c: None),
    Flyer("pricing", "Inventory List",
          "Every available home with plan, specs, price and move-in.",
          "flyers/pricing.html", _needs_homes),
    Flyer("plans", "Price Sheet",
          "Every plan with specs and starting price.",
          "flyers/plans.html", _needs_plans),
    Flyer("grid", "Photo Inventory",
          "Photo cards for every available home.",
          "flyers/grid.html", _needs_homes),
]
FLYERS_BY_KEY = {f.key: f for f in COMMUNITY_FLYERS}

HOME_FLYER = Flyer("home", "Inventory Flyer",
                   "Two pages: photo and facts, then floor plan.",
                   "flyers/home.html", lambda c: None)

# Rows per printed page. The first page carries the community header, so it
# holds fewer. Rows are single-line and fixed-height, which keeps these exact;
# the page template flags any sheet that still overflows.
PRICING_ROWS = (15, 15)   # inventory list: rows the Excel original fits above its footer
PLAN_ROWS = (12, 12)      # price sheet
GRID_CARDS = (10, 10)     # photo inventory: 2 columns x 5 rows


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
