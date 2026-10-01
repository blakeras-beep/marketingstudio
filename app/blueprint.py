"""Blueprint (Big Board) data for the flyers: previous price, sold status, HOA and tax rate.

Read from Blueprint's read-only GET /api/feed/marketing (BLUEPRINT_URL + MARKETING_FEED_TOKEN).
Until that's configured every lookup returns None and the flyers simply omit those lines.
Homes are matched on community + normalised street address (the BDX feed has no Blueprint key).
Best effort, like the photo store: a failed fetch keeps the last good copy and is shown on /healthz.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("studio.blueprint")
TTL = int(os.environ.get("BLUEPRINT_CACHE_TTL", "900"))
TIMEOUT = float(os.environ.get("BLUEPRINT_TIMEOUT", "15"))


def _key(s) -> str:
    from .seed import _key as k
    return k(s or "")


def _addr(s) -> str:
    from .seed import address_key
    return address_key(s or "")


def _pct(rate) -> str:
    """0.01909 -> '1.909%'."""
    return f"{rate * 100:.3f}".rstrip("0").rstrip(".") + "%"


class Blueprint:
    """Lookups over one fetched copy of the feed. The empty instance answers None/False everywhere."""

    def __init__(self, body: dict | None = None):
        body = body or {}
        self.homes = body.get("homes") or []
        self.communities = body.get("communities") or []
        self._by_comm: dict[tuple[str, str], dict] = {}
        by_addr: dict[str, list[dict]] = {}
        for h in self.homes:
            a = _addr(h.get("address1"))
            if not a:
                continue
            self._by_comm[(_key(h.get("community")), a)] = h
            by_addr.setdefault(a, []).append(h)
        # address alone only when it is unique across every community (no guessing between two)
        self._by_addr = {a: hs[0] for a, hs in by_addr.items() if len(hs) == 1}
        self._rules = {_key(c.get("community")): c for c in self.communities}

    def _rules_for(self, community) -> dict | None:
        k = _key(community.name)
        if k in self._rules:
            return self._rules[k]
        # Blueprint's names can carry a phase: "Mayfield Farms 4" -> Mayfield Farms
        pref = [r for rk, r in self._rules.items() if rk.startswith(k) or k.startswith(rk)]
        return pref[0] if len(pref) == 1 else None

    def home(self, community, home) -> dict | None:
        a = _addr(getattr(home, "address", None))
        if not a:
            return None
        return self._by_comm.get((_key(community.name), a)) or self._by_addr.get(a)

    def was_price(self, community, home) -> float | None:
        h = self.home(community, home)
        was = h.get("was_price") if h else None
        price = getattr(home, "price", None)
        # only when it is above the price the flyer prints (the BDX price can differ from Blueprint's)
        return was if was and price and was > price else None

    def is_sold(self, community, home) -> bool:
        h = self.home(community, home)
        return bool(h and h.get("sold"))

    def hoa(self, community) -> str | None:
        r = self._rules_for(community)
        annual = r.get("hoa_annual") if r else None
        return f"${annual:,.0f}/year" if annual else None

    def tax_rate(self, community) -> str | None:
        r = self._rules_for(community)
        rate = r.get("tax_rate") if r else None
        return _pct(rate) if rate else None

    def sold_addresses(self, community) -> set[str]:
        k = _key(community.name)
        return {_addr(h.get("address1")) for h in self.homes if h.get("sold") and _key(h.get("community")) == k}


NONE = Blueprint()


class _State:
    bp: Blueprint = NONE
    fetched_at: float | None = None
    checked_at: float = 0.0
    error: str | None = None


_state = _State()
_lock = threading.Lock()


def configured() -> bool:
    return bool(os.environ.get("BLUEPRINT_URL", "").strip() and os.environ.get("MARKETING_FEED_TOKEN", "").strip())


def _fetch() -> dict:
    url = os.environ["BLUEPRINT_URL"].strip().rstrip("/") + "/api/feed/marketing"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["MARKETING_FEED_TOKEN"].strip(),
                                               "User-Agent": "SandlinMarketingStudio/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def current(force: bool = False) -> Blueprint:
    """The latest Blueprint copy, refetched when older than BLUEPRINT_CACHE_TTL; never raises."""
    if not configured():
        return NONE
    with _lock:
        if force or time.monotonic() - _state.checked_at > TTL:
            _state.checked_at = time.monotonic()
            try:
                _state.bp = Blueprint(_fetch())
                _state.fetched_at = time.time()
                _state.error = None
            except Exception as e:   # keep the last good copy
                _state.error = f"{type(e).__name__}: {e}"
                log.warning("blueprint feed failed: %s", _state.error)
        return _state.bp


def status() -> dict:
    return {"configured": configured(), "homes": len(_state.bp.homes), "communities": len(_state.bp.communities),
            "fetched_at": _state.fetched_at, "error": _state.error}
