"""Sandlin Marketing Studio — community collateral rendered live from the BDX feed."""
from __future__ import annotations

import logging
import re

from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bdx, config, fmt, images, store
from .flyers import (
    COMMUNITY_FLYERS, FLYERS_BY_KEY, HOME_FLYER, GRID_CARDS, PLAN_ROWS, PRICING_ROWS,
    paginate, sorted_homes, sorted_plans,
)

HERE = Path(__file__).parent
log = logging.getLogger("marketingstudio")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Sandlin Marketing Studio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(fmt.FILTERS)
templates.env.globals.update(img=images.src)
templates.env.globals.update(DASH=fmt.DASH, READY=fmt.READY, DISCLAIMER=config.DISCLAIMER,
                             city_line=fmt.city_line, plan_label=fmt.plan_label)


def _feed_banner(st: bdx.FeedState) -> dict:
    age = None
    if st.fetched_at:
        age = int((datetime.now(timezone.utc) - st.fetched_at).total_seconds() // 60)
    return {"fetched_at": st.fetched_at, "age_min": age, "error": st.error}


def _community_or_404(slug: str) -> bdx.Community:
    st = bdx.state()
    c = bdx.community(slug)
    if c is None:
        if not st.communities and st.error:
            raise HTTPException(503, f"BDX feed unavailable: {st.error}")
        raise HTTPException(404, f"No community '{slug}' in the BDX feed")
    return c


def _doc_title(c: bdx.Community, name: str) -> str:
    # Becomes the default "Save as PDF" filename.
    safe = lambda s: "".join(ch if ch.isalnum() else "-" for ch in s).strip("-")
    return f"Sandlin-{safe(c.name)}-{safe(name)}-{date.today():%Y-%m-%d}"


@app.get("/img")
def image(u: str, w: int, h: int | None = None, la: int = 0):
    """Feed photo downsampled to print size (see app/images.py)."""
    if not images.allowed(u) or not (16 <= w <= 4000) or (h is not None and not 16 <= h <= 4000):
        raise HTTPException(400, "Image not allowed")
    # The store is a cache: if it fails, the photo is still rendered and served,
    # and the failure is logged. Only an unreachable/corrupt source fails the
    # image (the page then shows placeholder art).
    st, key = store.get_store(), images.cache_key(u, w, h, bool(la))
    public = st.public_url(key)
    try:
        if public and st.exists(key):
            return RedirectResponse(public, status_code=302)
        data = None if public else st.get(key)
    except Exception:
        log.exception("image store read failed (%s); rendering directly", st.name)
        data, public = None, None
    if data is None:
        try:
            data = images.render(u, w, h, bool(la))
        except Exception as e:
            log.warning("image source failed: %s: %s (%s)", type(e).__name__, e, u)
            raise HTTPException(502, f"Image unavailable: {type(e).__name__}")
        try:
            st.put(key, data)
            if public:
                return RedirectResponse(public, status_code=302)
        except Exception:
            log.exception("image store write failed (%s); serving directly", st.name)
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/healthz/images")
def healthz_images():
    """Step-by-step image pipeline check: feed photo fetch, resize, store write/read.

    Reports the exact error at each step so a misconfigured R2 setting is obvious.
    """
    out: dict = {"store": store.get_store().name, "r2_configured": store.r2_configured(),
                 "r2_setup_error": store.setup_error,
                 "r2_account_id": (store.R2_ACCOUNT_ID[:4] + "…") if store.R2_ACCOUNT_ID else None,
                 "r2_account_id_looks_valid": bool(re.fullmatch(r"[0-9a-f]{32}", store.R2_ACCOUNT_ID)),
                 "r2_bucket": store.R2_BUCKET or None, "r2_public_url": store.R2_PUBLIC_URL or None}
    try:
        return _check_images(out)
    except Exception as e:  # the diagnostic itself must never 500
        log.exception("image diagnostic failed")
        out["error"] = f"{type(e).__name__}: {e}"
        return JSONResponse(out, status_code=503)


def _check_images(out: dict) -> JSONResponse:
    cs = bdx.state().communities
    url = next((x for c in cs for x in [c.hero] if x and images.allowed(x)), None)
    out["sample"] = url

    def step(name, fn):
        try:
            res = fn()
            out[name] = "ok" if res is None else res
            return res if res is not None else True
        except Exception as e:
            out[name] = f"FAILED {type(e).__name__}: {e}"
            return None

    if not url:
        out["fetch_and_resize"] = "FAILED: no feed photo found (is the feed loading?)"
        return JSONResponse(out, status_code=503)
    data = step("fetch_and_resize", lambda: images.render(url, 400))
    if isinstance(data, bytes):
        out["fetch_and_resize"] = f"ok ({len(data)} bytes)"
        st, key = store.get_store(), "healthcheck-" + images.cache_key(url, 400, None, False)
        step("store_write", lambda: st.put(key, data))
        step("store_read", lambda: "ok" if st.get(key) == data else "FAILED: read back different bytes")
    ok = all(str(v).startswith("ok") for k, v in out.items() if k in
             ("fetch_and_resize", "store_write", "store_read"))
    return JSONResponse(out, status_code=200 if ok else 503)


@app.get("/livez")
def livez():
    """Process is up. Deploy health check; deliberately independent of the feed,
    so a BDX outage can't block a deploy (/healthz reports feed state)."""
    return {"ok": True}


@app.get("/healthz")
def healthz():
    st = bdx.state()
    ok = bool(st.communities)
    return JSONResponse(
        {"ok": ok, "communities": len(st.communities), "image_store": store.get_store().name,
         "fetched_at": st.fetched_at.isoformat() if st.fetched_at else None,
         "error": st.error},
        status_code=200 if ok else 503,
    )


@app.get("/", response_class=HTMLResponse)
def repository(request: Request):
    st = bdx.state()
    return templates.TemplateResponse(request, "index.html", {
        "communities": st.communities, "feed": _feed_banner(st),
    })


@app.get("/c/{slug}", response_class=HTMLResponse)
def community_page(request: Request, slug: str):
    c = _community_or_404(slug)
    flyers = [(f, f.needs(c)) for f in COMMUNITY_FLYERS]
    return templates.TemplateResponse(request, "community.html", {
        "c": c, "flyers": flyers, "homes": sorted_homes(c),
        "feed": _feed_banner(bdx.state()),
    })


@app.get("/c/{slug}/homes/{home}", response_class=HTMLResponse)
def home_flyer(request: Request, slug: str, home: str):
    c = _community_or_404(slug)
    h = c.home(home)
    if h is None:
        raise HTTPException(404, f"No home '{home}' in {c.name} — it may have sold")
    return templates.TemplateResponse(request, HOME_FLYER.template, {
        "c": c, "h": h, "flyer": HOME_FLYER, "today": date.today(),
        "doc_title": _doc_title(c, h.address or h.id),
        "back": f"/c/{c.slug}",
    })


@app.get("/c/{slug}/{key}", response_class=HTMLResponse)
def community_flyer(request: Request, slug: str, key: str):
    c = _community_or_404(slug)
    f = FLYERS_BY_KEY.get(key)
    if f is None:
        raise HTTPException(404, f"Unknown flyer '{key}'")
    reason = f.needs(c)
    if reason:
        raise HTTPException(409, f"{f.title} unavailable for {c.name}: {reason}")
    ctx = {"c": c, "flyer": f, "today": date.today(),
           "doc_title": _doc_title(c, f.title), "back": f"/c/{c.slug}"}
    if key == "pricing":
        ctx["pages"] = paginate(sorted_homes(c), *PRICING_ROWS)
    elif key == "plans":
        ctx["pages"] = paginate(sorted_plans(c), *PLAN_ROWS)
    elif key == "grid":
        ctx["pages"] = paginate(sorted_homes(c), *GRID_CARDS)
    return templates.TemplateResponse(request, f.template, ctx)


# JSON for debugging and for other tools (e.g. Sales Desk) to consume.
@app.get("/api/communities")
def api_communities():
    st = bdx.state()
    return {"fetched_at": st.fetched_at, "error": st.error,
            "communities": [{"name": c.name, "slug": c.slug, "status": c.status,
                             "homes": len(c.homes), "plans": len(c.plans)}
                            for c in st.communities]}


@app.get("/api/communities/{slug}")
def api_community(slug: str):
    return _community_or_404(slug).to_dict()


@app.post("/api/refresh")
def api_refresh():
    st = bdx.state(force=True)
    return {"communities": len(st.communities), "fetched_at": st.fetched_at, "error": st.error}
