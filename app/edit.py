"""Marketing inputs editor (admin + marketing). CSMs never see these routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, bdx, blueprint, inputs
from .flyers import sorted_plans
from .web import templates

router = APIRouter()
EDITORS = ("admin", "marketing")


def _community(slug: str) -> bdx.Community:
    c = bdx.community(slug)
    if c is None:
        raise HTTPException(404, f"No community '{slug}' in the BDX feed")
    return c


def _done(slug: str, section: str, n: int) -> RedirectResponse:
    msg = "No changes." if n == 0 else f"Saved {n} change{'s' if n != 1 else ''}."
    return RedirectResponse(f"/c/{slug}/edit?msg={msg}#{section}", status_code=303)


@router.get("/c/{slug}/edit", response_class=HTMLResponse)
def edit_page(request: Request, slug: str, msg: str = ""):
    auth.require(request, *EDITORS)
    c = _community(slug)
    vals = inputs.load()
    cs = inputs.community_subject(c)
    return templates.TemplateResponse(request, "edit.html", {
        "c": c, "msg": msg, "vals": vals, "cs": cs,
        "plans": sorted_plans(c),
        "plan_subject": lambda p: inputs.plan_subject(c, p.name or ""),
        "F": inputs, "last": inputs.last_edit("community", [cs]),
        "bp": blueprint.current(),
    })


@router.post("/c/{slug}/edit/community")
async def save_community(request: Request, slug: str):
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")
    user = auth.require(request, *EDITORS)
    c = _community(slug)
    form = await request.form()
    values = {}
    for f in inputs.COMMUNITY_FIELDS:
        if f.kind in ("photo", "photos"):   # only photos that are in this community's feed
            picked = [u for u in form.getlist(f.key) if u in c.photos]
            values[f.key] = "\n".join(picked[:1] if f.kind == "photo" else picked[:3])
        else:
            values[f.key] = form.get(f.key, "")
    n = inputs.save("community", inputs.community_subject(c), values, user["id"])
    return _done(c.slug, "community", n)


@router.post("/c/{slug}/edit/plans")
async def save_plans(request: Request, slug: str):
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")
    user = auth.require(request, *EDITORS)
    c = _community(slug)
    form = await request.form()
    n = 0
    for i, p in enumerate(sorted_plans(c)):
        n += inputs.save("plan", inputs.plan_subject(c, p.name or ""),
                         {f.key: form.get(f"{i}.{f.key}", "") for f in inputs.PLAN_FIELDS}, user["id"])
    return _done(c.slug, "plans", n)


@router.post("/c/{slug}/homes/{home}/edit")
async def save_home(request: Request, slug: str, home: str):
    """Saved from the home's own flyer (the Edit panel), and back to it."""
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")
    user = auth.require(request, *EDITORS)
    c = _community(slug)
    h = c.home(home)
    if h is None:
        raise HTTPException(404, "That home is no longer in the feed")
    form = await request.form()
    n = inputs.save("home", h.id, {f.key: form.get(f.key, "") for f in inputs.HOME_FIELDS}, user["id"])
    msg = "No changes." if n == 0 else "Saved."
    return RedirectResponse(f"/c/{c.slug}/homes/{h.slug}?msg={msg}", status_code=303)
