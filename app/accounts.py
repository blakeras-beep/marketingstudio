"""Sign in / out, your account, and user administration (admin only)."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth
from .web import templates

router = APIRouter()
SECURE_COOKIE = os.environ.get("COOKIE_SECURE", "1") != "0"


def _back(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse(f"{url}?{q}" if q else url, status_code=303)


def _check_post(request: Request) -> None:
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")


def _set_session_cookie(resp, request: Request, user_id: int) -> None:
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.start_session(user_id), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=SECURE_COOKIE and https, path="/")


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", err: str = ""):
    if auth.current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "err": err})


@router.post("/login")
async def login(request: Request):
    _check_post(request)
    form = await request.form()
    nxt = _safe_next(form.get("next"))
    user = auth.authenticate(form.get("email", ""), form.get("password", ""))
    if not user:
        from urllib.parse import quote
        return RedirectResponse(f"/login?next={quote(nxt)}&err=Email+or+password+is+incorrect", status_code=303)
    resp = RedirectResponse(nxt, status_code=303)
    _set_session_cookie(resp, request, user["id"])
    return resp


@router.post("/logout")
def logout(request: Request):
    _check_post(request)
    auth.end_session(request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@router.get("/account", response_class=HTMLResponse)
def account(request: Request, msg: str = "", err: str = ""):
    auth.require(request)
    return templates.TemplateResponse(request, "account.html", {"msg": msg, "err": err})


@router.post("/account/password")
async def change_password(request: Request):
    _check_post(request)
    user = auth.require(request)
    form = await request.form()
    if not auth.authenticate(user["email"], form.get("current", "")):
        return _back("/account", err="Your current password is incorrect.")
    if form.get("new") != form.get("confirm"):
        return _back("/account", err="The new passwords don't match.")
    try:
        auth.set_password(user["id"], form.get("new", ""))
    except ValueError as e:
        return _back("/account", err=str(e))
    # Changing a password signs you out everywhere; start a fresh session here.
    resp = _back("/account", msg="Password changed.")
    _set_session_cookie(resp, request, user["id"])
    return resp


# ---------------------------------------------------------------- admin

@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, msg: str = "", err: str = ""):
    auth.require(request, "admin")
    return templates.TemplateResponse(request, "users.html", {
        "users": auth.list_users(), "roles": auth.ROLES, "msg": msg, "err": err,
        "min_password": auth.MIN_PASSWORD,
    })


@router.post("/admin/users")
async def create_user(request: Request):
    _check_post(request)
    me = auth.require(request, "admin")
    f = await request.form()
    try:
        u = auth.create_user(f.get("email", ""), f.get("name", ""), f.get("role", ""), f.get("password", ""), me["id"])
    except ValueError as e:
        return _back("/admin/users", err=str(e))
    return _back("/admin/users", msg=f"Created {u['name']} ({auth.ROLE_LABELS[u['role']]}). "
                                     "Share the temporary password with them directly.")


@router.post("/admin/users/{user_id}")
async def update_user(request: Request, user_id: int):
    _check_post(request)
    auth.require(request, "admin")
    f = await request.form()
    try:
        auth.update_user(user_id, name=f.get("name"), role=f.get("role"),
                         active=None if f.get("active") is None else f.get("active") == "1")
    except ValueError as e:
        return _back("/admin/users", err=str(e))
    return _back("/admin/users", msg="Saved.")


@router.post("/admin/users/{user_id}/password")
async def reset_password(request: Request, user_id: int):
    _check_post(request)
    auth.require(request, "admin")
    f = await request.form()
    try:
        auth.set_password(user_id, f.get("password", ""))
    except ValueError as e:
        return _back("/admin/users", err=str(e))
    return _back("/admin/users", msg="Password reset. They've been signed out everywhere.")
