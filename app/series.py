"""Brand series a community is sold under. Set per community on its Settings page (admin and
marketing director); drives the logo and flyer design (body class `tier-<key>`). A new series is
a new entry here plus its logo and styles."""
from __future__ import annotations

from . import bdx, db

SERIES = {
    "homes": "Sandlin Homes",
    "signature": "Sandlin Signature",
}
DEFAULT = "homes"
# Logo per series and use: navy = on white/light, white-wide / white-stacked = on navy or photos.
LOGOS = {
    "homes": {"navy": "/static/logo-navy.png", "white-wide": "/static/brand/logo-white-wide.png",
              "white-stacked": "/static/brand/logo-white-stacked.png"},
    "signature": {"navy": "/static/logo-signature.png", "white-wide": "/static/brand/logo-signature-white-wide.png",
                  "white-stacked": "/static/brand/logo-signature-white-stacked.png"},
}


def logo(c, use: str) -> str:
    return LOGOS.get(c.tier, LOGOS[DEFAULT])[use]


def label(c) -> str:
    return SERIES.get(c.tier, SERIES[DEFAULT])


def refresh() -> None:
    """Load every community's saved series into bdx (read by Community.tier)."""
    with db.connect() as c:
        rows = c.q("SELECT subject, value FROM inputs WHERE scope = 'community' AND field = 'series'")
    bdx.SERIES_OVERRIDES = {r["subject"]: r["value"] for r in rows if r["value"] in SERIES}
