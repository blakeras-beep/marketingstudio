"""BDX feed: fetch, cache, parse into the binding context the flyers render from.

The feed is the only source of facts. Anything the feed does not carry is left
as None and prints as an em dash; nothing is filled in or guessed.

Parsing is deliberately forgiving: namespaces are stripped, tag names match
case-insensitively, and each field accepts the handful of spellings BDX
producers use (e.g. SpecAddress is a container of SpecStreet1/SpecCity/...,
phone is AreaCode/Prefix/Suffix, Schools sits at Subdivision level).
"""
from __future__ import annotations

import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

from . import config


# ---------------------------------------------------------------- model

@dataclass
class Plan:
    name: str | None
    type: str | None
    base_price: float | None
    price_from: float | None  # BasePrice, else lowest available spec price
    sqft: int | None
    beds: float | None
    baths: float | None
    half_baths: float | None
    garage: float | None
    stories: float | None
    description: str | None
    elevation: str | None
    floorplans: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    homes_available: int = 0


@dataclass
class Home:
    id: str
    slug: str
    address: str | None
    plan: str | None
    city: str | None
    state: str | None
    zip: str | None
    price: float | None
    sqft: int | None
    beds: float | None
    baths: float | None
    half_baths: float | None
    garage: float | None
    stories: float | None
    move_in: date | None
    description: str | None
    hero: str | None
    floorplans: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)


@dataclass
class Contact:
    agents: list[str] = field(default_factory=list)
    phone: str | None = None
    email: str | None = None
    hours: str | None = None
    street: str | None = None  # SalesOffice/Address
    city: str | None = None
    state: str | None = None
    zip: str | None = None


@dataclass
class Schools:
    district: str | None = None
    elementary: str | None = None
    middle: str | None = None
    high: str | None = None


@dataclass
class Community:
    name: str
    slug: str
    status: str | None
    street: str | None
    city: str | None
    state: str | None
    zip: str | None
    description: str | None
    driving_directions: str | None
    website: str | None
    photos: list[str]
    schools: Schools
    contact: Contact
    plans: list[Plan]
    homes: list[Home]
    brand: str | None = None  # BDX <Builder><BrandName>
    number: str | None = None  # BDX SubdivisionNumber
    lot_map: str | None = None  # SubImage Type="LotMap"

    @property
    def tier(self) -> str:
        """'signature' (gold arch, navy + champagne only) or 'homes'."""
        if self.brand and "signature" in self.brand.lower():
            return "signature"
        keys = {name_key(n) for n in config.SIGNATURE_COMMUNITIES}
        return "signature" if name_key(self.name) in keys else "homes"

    # Derived facts — computed from feed values only.
    @property
    def price_from(self) -> float | None:
        vals = [p.price_from for p in self.plans if p.price_from]
        vals += [h.price for h in self.homes if h.price]
        return min(vals) if vals else None

    @property
    def sqft_range(self) -> tuple[int, int] | None:
        vals = [p.sqft for p in self.plans if p.sqft] + [h.sqft for h in self.homes if h.sqft]
        return (min(vals), max(vals)) if vals else None

    @property
    def hero(self) -> str | None:
        if self.photos:
            return self.photos[0]
        for h in self.homes:
            if h.hero:
                return h.hero
        for p in self.plans:
            if p.elevation:
                return p.elevation
        return None

    def home(self, slug: str) -> Home | None:
        return next((h for h in self.homes if h.slug == slug), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price_from"] = self.price_from
        d["sqft_range"] = self.sqft_range
        d["hero"] = self.hero
        d["tier"] = self.tier
        return d


# ---------------------------------------------------------------- helpers

def slugify(s: str) -> str:
    s = re.sub(r"['’]", "", s.lower())  # Settler's Glen -> settlers-glen
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def name_key(s: str) -> str:
    """Forgiving community-name match key (case/punctuation/spacing-insensitive)."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _kids(el, *names):
    want = {n.lower() for n in names}
    return [c for c in el if _local(c.tag) in want]


def _kid(el, *names):
    """First direct child matching any name, in the order names are given."""
    if el is None:
        return None
    for n in names:
        for c in el:
            if _local(c.tag) == n.lower():
                return c
    return None


def _txt(el) -> str | None:
    if el is None:
        return None
    t = " ".join("".join(el.itertext()).split())
    return t or None


def _t(el, *names) -> str | None:
    return _txt(_kid(el, *names))


def _para(el, *names) -> str | None:
    """Like _t but keeps paragraph breaks (one per line) for long copy."""
    k = _kid(el, *names)
    if k is None:
        return None
    lines = [" ".join(ln.split()) for ln in "".join(k.itertext()).splitlines()]
    return "\n".join(ln for ln in lines if ln) or None


def _all(el, *names) -> str | None:
    """Every matching child's text, joined — e.g. two <Middle> schools."""
    if el is None:
        return None
    vals = []
    for c in _kids(el, *names):
        t = _txt(c)
        if t and t not in vals:
            vals.append(t)
    return ", ".join(vals) or None


def _attr(el, *names) -> str | None:
    if el is None:
        return None
    lower = {k.rsplit("}", 1)[-1].lower(): v for k, v in el.attrib.items()}
    for n in names:
        v = (lower.get(n.lower()) or "").strip()
        if v:
            return v
    return None


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _pos(s: str | None) -> float | None:
    """Positive number or None — BDX producers send 0 for 'not priced'."""
    v = _num(s)
    return v if v and v > 0 else None


def _int(s: str | None) -> int | None:
    v = _pos(s)
    return int(round(v)) if v else None


def _images(el, *names, keep=lambda c: True) -> list[str]:
    """Image URLs from matching children, ordered by SequencePosition when present.

    An image flagged IsPreferredSubImage="1" sorts first (it's the feed's hero).
    """
    if el is None:
        return []
    found = []
    for i, c in enumerate(_kids(el, *names)):
        if not keep(c):
            continue
        url = _txt(c) or _attr(c, "url", "src", "href")
        if url and re.match(r"https?://", url):
            seq = _num(_attr(c, "SequencePosition", "Sequence", "Position"))
            pref = 0 if _attr(c, "IsPreferredSubImage") == "1" else 1
            found.append((pref, seq if seq is not None else 1e9, i, url))
    out = []
    for *_, url in sorted(found):
        if url not in out:
            out.append(url)
    return out


def _phone(el) -> str | None:
    if el is None:
        return None
    ac, pre, suf = _t(el, "AreaCode"), _t(el, "Prefix"), _t(el, "Suffix")
    if ac and pre and suf:
        s = f"{ac}.{pre}.{suf}"
        ext = _t(el, "Extension")
        return f"{s} x{ext}" if ext else s
    raw = _txt(el)
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:]}"
    return raw


def _date(el) -> date | None:
    if el is None:
        return None
    y, m, d = _int(_t(el, "Year")), _int(_t(el, "Month")), _int(_t(el, "Day"))
    if y and m:
        try:
            return date(y, m, d or 1)
        except ValueError:
            return None
    raw = _txt(el)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%m/%d/%y"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- parse

def _parse_plan(el) -> Plan:
    imgs = _kid(el, "PlanImages")
    src = imgs if imgs is not None else el
    elev = _images(src, "ElevationImage")
    return Plan(
        name=_t(el, "PlanName"),
        type=_attr(el, "Type"),
        base_price=_pos(_t(el, "BasePrice")),
        price_from=None,
        sqft=_int(_t(el, "BaseSqft", "Sqft")),
        beds=_pos(_t(el, "Bedrooms")),
        baths=_pos(_t(el, "Baths")),
        half_baths=_pos(_t(el, "HalfBaths")),
        garage=_pos(_t(el, "Garage")),
        stories=_pos(_t(el, "Stories")),
        description=_para(el, "Description", "PlanDescription"),
        elevation=elev[0] if elev else None,
        floorplans=_images(src, "FloorPlanImage"),
        photos=elev + _images(src, "InteriorImage"),
    )


def _parse_spec(el, plan: Plan | None, used: set[str]) -> Home:
    addr = _kid(el, "SpecAddress")
    street = _t(addr, "SpecStreet1", "Street1", "Street") if addr is not None else None
    if street and addr is not None:
        s2 = _t(addr, "SpecStreet2", "Street2")
        if s2:
            street = f"{street} {s2}"
    sid = _t(el, "SpecNumber") or _attr(el, "SpecNumber", "SpecID", "Id") or street or "home"
    slug = slugify(street or sid)
    base, n = slug, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)

    imgs = _kid(el, "SpecImages")
    src = imgs if imgs is not None else el
    elev = _images(src, "SpecElevationImage", "ElevationImage")
    interior = _images(src, "SpecInteriorImage", "InteriorImage")
    floors = _images(src, "SpecFloorPlanImage", "FloorPlanImage")
    if plan:  # a spec with no pictures of its own shows its plan's
        elev = elev or ([plan.elevation] if plan.elevation else [])
        floors = floors or list(plan.floorplans)

    def a(*names):
        return _t(addr, *names) if addr is not None else None

    return Home(
        id=sid,
        slug=slug,
        address=street,
        plan=plan.name if plan else _t(el, "PlanName", "SpecPlanName"),
        city=a("SpecCity", "City"),
        state=a("SpecState", "State"),
        zip=a("SpecZIP", "SpecZip", "ZIP", "Zip"),
        price=_pos(_t(el, "SpecPrice", "Price")),
        sqft=_int(_t(el, "SpecSqft", "Sqft")),
        beds=_pos(_t(el, "SpecBedrooms", "Bedrooms")),
        baths=_pos(_t(el, "SpecBaths", "Baths")),
        half_baths=_pos(_t(el, "SpecHalfBaths", "HalfBaths")),
        garage=_pos(_t(el, "SpecGarage", "Garage")),
        stories=_pos(_t(el, "SpecStories", "Stories")),
        move_in=_date(_kid(el, "SpecMoveInDate", "MoveInDate")),
        description=_para(el, "SpecDescription", "Description"),
        hero=elev[0] if elev else (interior[0] if interior else None),
        floorplans=floors,
        photos=elev + interior,
    )


def _parse_schools(sub) -> Schools:
    out = Schools()
    el = _kid(sub, "Schools")
    if el is None:
        return out
    out.district = _t(el, "DistrictName", "SchoolDistrict", "District")
    if out.district is None:
        d = _kid(el, "SchoolDistrict")
        out.district = _attr(d, "Name") if d is not None else None
    levels = {
        "elementary": ("Elementary", "ElementarySchool"),
        "middle": ("Middle", "MiddleSchool", "JuniorHigh", "Intermediate"),
        "high": ("High", "HighSchool", "SeniorHigh"),
    }
    for key, names in levels.items():
        setattr(out, key, _all(el, *names))
    # <School Type="Elementary"><SchoolName>…</SchoolName></School> form, anywhere below.
    for s in el.iter():
        if _local(s.tag) != "school":
            continue
        typ = (_attr(s, "Type", "Level") or "").lower()
        nm = _t(s, "SchoolName", "Name") or (_txt(s) if len(s) == 0 else None)
        for key, names in levels.items():
            if nm and getattr(out, key) is None and any(n.lower() in typ for n in names[:1]):
                setattr(out, key, nm)
    return out


def _parse_contact(sub) -> Contact:
    so = _kid(sub, "SalesOffice")
    c = Contact()
    if so is None:
        return c
    for ag in _kids(so, "Agent"):
        nm = _txt(ag)
        if nm and nm not in c.agents:
            c.agents.append(nm)
    c.phone = _phone(_kid(so, "Phone"))
    c.email = _t(so, "Email")
    c.hours = _t(so, "Hours")
    addr = _kid(so, "Address")
    if addr is not None:
        c.street = _t(addr, "Street1")
        c.city, c.state, c.zip = _t(addr, "City"), _t(addr, "State"), _t(addr, "ZIP", "Zip")
    return c


def _parse_subdivision(sub, brand: str | None = None) -> Community | None:
    name = _t(sub, "SubdivisionName")
    if not name:
        return None
    addr = _kid(sub, "SubAddress")

    def a(*names):
        return _t(addr, *names) if addr is not None else None

    plans: list[Plan] = []
    homes: list[Home] = []
    used: set[str] = set()
    for pel in _kids(sub, "Plan"):
        plan = _parse_plan(pel)
        # <PlanNotAvailable>1</PlanNotAvailable>: no longer offered. Its specs
        # are still real inventory, but the plan isn't marketed.
        if _t(pel, "PlanNotAvailable") != "1":
            plans.append(plan)
        mine = [_parse_spec(s, plan, used) for s in _kids(pel, "Spec")]
        homes += mine
        plan.homes_available = len(mine)
        priced = [h.price for h in mine if h.price]
        plan.price_from = plan.base_price or (min(priced) if priced else None)
    # Specs that sit directly under the Subdivision have no parent plan.
    homes += [_parse_spec(s, None, used) for s in _kids(sub, "Spec")]

    contact = _parse_contact(sub)
    is_map = lambda c: (_attr(c, "Type") or "").lower() == "lotmap"
    maps = _images(sub, "SubImage", keep=is_map)
    # Sandlin's feed has no SubAddress; the in-community sales office address
    # is the community's address.
    return Community(
        name=name,
        slug=slugify(name),
        status=_attr(sub, "Status") or _t(sub, "Status"),
        street=a("SubStreet1", "Street1") or contact.street,
        city=a("SubCity", "City") or contact.city,
        state=a("SubState", "State") or contact.state,
        zip=a("SubZIP", "SubZip", "ZIP") or contact.zip,
        description=_para(sub, "SubDescription", "Description"),
        driving_directions=_para(sub, "DrivingDirections"),
        website=_t(sub, "SubWebsite", "Website"),
        photos=_images(sub, "SubImage", keep=lambda c: not is_map(c)),
        schools=_parse_schools(sub),
        contact=contact,
        plans=plans,
        homes=homes,
        brand=brand,
        number=_t(sub, "SubdivisionNumber"),
        lot_map=maps[0] if maps else None,
    )


def parse(xml_bytes: bytes) -> list[Community]:
    root = ET.fromstring(xml_bytes)
    builders = [b for b in root.iter() if _local(b.tag) == "builder"] or [root]
    subs = []
    for b in builders:
        brand = _t(b, "BrandName") if b is not root else None
        subs += [(s, brand) for s in b.iter() if _local(s.tag) == "subdivision"]
    # The same subdivision can be listed under more than one Builder (Sandlin's
    # feed repeats three under "New Global"). Keep one, preferring the Sandlin brand.
    unique: dict[str, Community] = {}
    for el, brand in subs:
        c = _parse_subdivision(el, brand)
        if not c:
            continue
        key = c.number or c.name
        prev = unique.get(key)
        if prev is None or ("sandlin" in (c.brand or "").lower()
                            and "sandlin" not in (prev.brand or "").lower()):
            unique[key] = c
    out: list[Community] = []
    seen: set[str] = set()
    for c in unique.values():
        slug, n = c.slug, 2
        while slug in seen:
            slug, n = f"{c.slug}-{n}", n + 1
        c.slug = slug
        seen.add(slug)
        out.append(c)
    out.sort(key=lambda c: c.name.lower())
    return out


# ---------------------------------------------------------------- cache

@dataclass
class FeedState:
    communities: list[Community] = field(default_factory=list)
    fetched_at: datetime | None = None   # last successful fetch+parse
    checked_at: float = 0.0              # monotonic time of last attempt
    error: str | None = None             # last failure, if the latest attempt failed


_state = FeedState()
_lock = threading.Lock()


def _fetch() -> bytes:
    req = urllib.request.Request(
        config.BDX_FEED_URL, headers={"User-Agent": "SandlinMarketingStudio/1.0"}
    )
    with urllib.request.urlopen(req, timeout=config.BDX_TIMEOUT) as r:
        return r.read()


def state(force: bool = False) -> FeedState:
    """Current feed, refetched when older than BDX_CACHE_TTL.

    On failure the last good parse keeps serving and `error` explains why.
    """
    with _lock:
        stale = time.monotonic() - _state.checked_at > config.BDX_CACHE_TTL
        if force or stale or _state.fetched_at is None:
            _state.checked_at = time.monotonic()
            try:
                _state.communities = parse(_fetch())
                _state.fetched_at = datetime.now(timezone.utc)
                _state.error = None
            except Exception as e:  # network, HTTP, XML — keep last good data
                _state.error = f"{type(e).__name__}: {e}"
        return _state


def community(slug_or_name: str) -> Community | None:
    cs = state().communities
    for c in cs:
        if c.slug == slug_or_name:
            return c
    key = name_key(slug_or_name)
    return next((c for c in cs if name_key(c.name) == key), None)
