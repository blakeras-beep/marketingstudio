"""General marketing assets: promos, warranty info and other sales collateral that isn't tied to
one community. Everyone signed in can view and download; admin and marketing director upload and
delete. Files live in the asset store (R2), their details in the `assets` table."""
from __future__ import annotations

import os
import re
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import UploadFile

from . import auth, db, store
from .auth import now

router = APIRouter()
EDITORS = ("admin", "marketing")
CATEGORIES = ("Promotions", "Warranty", "Sales collateral", "Other")
MAX_MB = int(os.environ.get("MAX_ASSET_MB", "50"))
# Extension -> type. Only these are accepted; PDFs, images and video open in the browser,
# everything else downloads. Nothing is ever served as HTML or script.
TYPES = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}
INLINE = ("application/pdf", "image/", "video/")


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).strip() or "file"
    return re.sub(r"[^\w.\- ()&]", "_", name)[:120]


def listing() -> list[dict]:
    with db.connect() as c:
        rows = c.q("SELECT a.*, u.name AS uploader FROM assets a LEFT JOIN users u ON u.id = a.uploaded_by "
                   "ORDER BY a.uploaded_at DESC, a.id DESC")
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(rows, key=lambda r: order.get(r["category"], len(order)))


def _back(msg: str = "", err: str = "") -> RedirectResponse:
    q = f"?msg={quote(msg)}" if msg else (f"?err={quote(err)}" if err else "")
    return RedirectResponse(f"/{q}#assets", status_code=303)


@router.post("/assets")
async def upload(request: Request):
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")
    user = auth.require(request, *EDITORS)
    form = await request.form()
    f = form.get("file")
    title = " ".join((form.get("title") or "").split())[:120]
    category = form.get("category") if form.get("category") in CATEGORIES else "Other"
    if not isinstance(f, UploadFile) or not f.filename:
        return _back(err="Choose a file to upload.")
    name = _safe_name(f.filename)
    ctype = TYPES.get(_ext(name))
    if ctype is None:
        return _back(err=f"{name}: that file type isn't accepted. Use PDF, an image, video, Word, Excel, PowerPoint or ZIP.")
    data = await f.read(MAX_MB * 1024 * 1024 + 1)
    if len(data) > MAX_MB * 1024 * 1024:
        return _back(err=f"{name} is over {MAX_MB} MB.")
    if not data:
        return _back(err=f"{name} is empty.")
    key = f"{secrets.token_hex(8)}/{name}"
    store.get_asset_store().put(key, data, content_type=ctype, cache_control="private, max-age=3600")
    with db.connect() as c:
        c.x("INSERT INTO assets (title, category, filename, content_type, size, key, uploaded_by, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title or name.rsplit(".", 1)[0], category, name, ctype, len(data), key, user["id"], now()))
    return _back(msg=f"Uploaded {name}.")


@router.get("/assets/{asset_id}/{filename}")
def download(request: Request, asset_id: int, filename: str):
    auth.require(request)
    with db.connect() as c:
        a = c.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if a is None:
        raise HTTPException(404, "That file has been removed")
    data = store.get_asset_store().get(a["key"])
    if data is None:
        raise HTTPException(404, "The file is missing from storage")
    inline = a["content_type"].startswith(INLINE)
    disp = "inline" if inline and request.query_params.get("download") != "1" else "attachment"
    return Response(data, media_type=a["content_type"], headers={
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(a['filename'])}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'",
    })


@router.post("/assets/{asset_id}/delete")
def delete(request: Request, asset_id: int):
    if not auth.same_origin(request):
        raise HTTPException(403, "Cross-site form posts are not allowed")
    auth.require(request, *EDITORS)
    with db.connect() as c:
        a = c.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
        if a is None:
            return _back(msg="Already removed.")
        c.x("DELETE FROM assets WHERE id = ?", (asset_id,))
    try:
        store.get_asset_store().delete(a["key"])
    except Exception:   # the listing is what people see; a leftover object is harmless
        pass
    return _back(msg=f"Removed {a['filename']}.")
