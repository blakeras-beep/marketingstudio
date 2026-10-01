"""SOLD homes stay on the flyers until they close.

The website (BDX) feed drops a home once it sells; Blueprint keeps it, marked sold, until it
settles. Every home the feed lists is remembered here, and after each feed load any remembered
home Blueprint still calls sold is put back into its community (flyers then stamp it SOLD).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone

from . import bdx, blueprint, db
from .seed import address_key

FORGET_AFTER_DAYS = 400   # a home unseen this long is not coming back


def _community_key(c) -> str:
    return c.number or c.slug


def _to_json(h: bdx.Home) -> str:
    return json.dumps(dataclasses.asdict(h), default=lambda v: v.isoformat() if isinstance(v, date) else str(v))


def _from_json(s: str) -> bdx.Home | None:
    d = json.loads(s)
    names = {f.name for f in dataclasses.fields(bdx.Home)}
    d = {k: v for k, v in d.items() if k in names}
    if d.get("move_in"):
        d["move_in"] = date.fromisoformat(d["move_in"])
    try:
        return bdx.Home(**d)
    except TypeError:   # the Home model changed since this copy was saved
        return None


def remember(communities) -> None:
    now = datetime.now(timezone.utc)
    with db.connect() as c:
        for com in communities:
            for h in com.homes:
                c.x("INSERT INTO homes_seen (community, home_id, address_key, data, seen_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (community, home_id) DO UPDATE SET address_key = excluded.address_key, "
                    "data = excluded.data, seen_at = excluded.seen_at",
                    (_community_key(com), h.id, address_key(h.address or ""), _to_json(h), now.isoformat(timespec="seconds")))
        cutoff = datetime.fromtimestamp(now.timestamp() - FORGET_AFTER_DAYS * 86400, timezone.utc)
        c.x("DELETE FROM homes_seen WHERE seen_at < ?", (cutoff.isoformat(timespec="seconds"),))


def restore_sold(communities, bp: blueprint.Blueprint | None = None) -> int:
    """Append remembered homes that left the feed but Blueprint still calls sold. Returns how many."""
    bp = bp or blueprint.current()
    added = 0
    with db.connect() as c:
        for com in communities:
            sold = bp.sold_addresses(com)
            if not sold:
                continue
            listed = {address_key(h.address or "") for h in com.homes}
            for r in c.q("SELECT address_key, data FROM homes_seen WHERE community = ? ORDER BY seen_at DESC",
                         (_community_key(com),)):
                if r["address_key"] in sold and r["address_key"] not in listed:
                    h = _from_json(r["data"])
                    if h:
                        com.homes.append(h)
                        listed.add(r["address_key"])
                        added += 1
    return added


def on_feed_load(communities) -> None:
    remember(communities)
    restore_sold(communities)
