"""Blueprint (Big Board) data for the flyers: previous price, sold status, HOA and tax rate.

Read from Blueprint's read-only GET /api/feed/marketing (BLUEPRINT_URL + MARKETING_FEED_TOKEN).
Until that's configured every lookup returns None and the flyers simply omit those lines.
Homes are matched on community + normalised street address (the BDX feed has no Blueprint key).
"""
from __future__ import annotations


class Blueprint:
    def was_price(self, community, home) -> float | None:
        return None

    def is_sold(self, community, home) -> bool:
        return False

    def hoa(self, community) -> str | None:
        return None

    def tax_rate(self, community) -> str | None:
        return None


NONE = Blueprint()


def current() -> Blueprint:
    return NONE
