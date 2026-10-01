"""Sandlin Marketing Studio — community collateral rendered live from the BDX feed."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bdx, config, fmt, images
from .flyers import (
    COMMUNITY_FLYERS, FLYERS_BY_KEY, HOME_FLYER, GRID_CARDS, PLAN_ROWS, PRICING_ROWS,
    paginate, sorted_homes, sorted_plans,
)

HERE = Path(__file__).parent
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
    try:
        path = images.render(u, w, h, bool(la))
    except Exception as e:  # unreachable/corrupt source: the page shows placeholder art
        raise HTTPException(502, f"Image unavailable: {type(e).__name__}")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/healthz")
def healthz():
    st = bdx.state()
    ok = bool(st.communities)
    return JSONResponse(
        {"ok": ok, "communities": len(st.communities),
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
