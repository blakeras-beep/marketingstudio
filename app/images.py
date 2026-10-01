"""Print-size image proxy.

Feed photos are often photographer originals (up to ~6000 px, 9-12 MB). The
browser embeds them in the PDF untouched, so a two-page flyer could reach
30 MB. This fetches each photo once, downsamples it to the size it actually
prints at, and keeps the result in R2 (see app/store.py).

Only hosts the feed uses are fetched; anything else is served as-is.
"""
from __future__ import annotations

import hashlib
import io
import os
import urllib.request
from urllib.parse import quote, urlparse

from PIL import Image, ImageOps

from . import config

ALLOWED = {
    "s3.amazonaws.com": "/buildercloud/",
    "www.sandlinhomes.com": "/",
}
DPI = int(os.environ.get("IMAGE_DPI", "200"))  # print resolution for photos
LINE_ART_DPI = 300   # floor plans: thin lines need more pixels
MAX_SOURCE_BYTES = 40 * 1024 * 1024


def allowed(url: str) -> bool:
    u = urlparse(url)
    prefix = ALLOWED.get(u.netloc)
    return u.scheme == "https" and prefix is not None and u.path.startswith(prefix)


def src(url: str | None, width_in: float, height_in: float | None = None,
        line_art: bool = False) -> str | None:
    """URL for `url` sized to print at width_in x height_in inches."""
    if not url or not allowed(url):
        return url
    dpi = LINE_ART_DPI if line_art else DPI
    q = f"/img?u={quote(url, safe='')}&w={round(width_in * dpi)}"
    if height_in:
        q += f"&h={round(height_in * dpi)}"
    if line_art:
        q += "&la=1"
    return q


def cache_key(url: str, w: int, h: int | None, la: bool) -> str:
    return hashlib.sha1(f"{url}|{w}|{h}|{int(la)}".encode()).hexdigest() + ".jpg"


def render(url: str, w: int, h: int | None = None, la: bool = False) -> bytes:
    """Fetch and downsample (never upsample); returns JPEG bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "SandlinMarketingStudio/1.0"})
    with urllib.request.urlopen(req, timeout=config.BDX_TIMEOUT) as r:
        data = r.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("source image too large")

    im = Image.open(io.BytesIO(data))
    # Already at or under print size: a JPEG is served byte-for-byte, since
    # re-encoding would only lose quality.
    if im.format == "JPEG" and im.width <= w and (h is None or im.height <= h):
        return data

    icc = im.info.get("icc_profile")  # keep the photo's color space
    im.draft("RGB", (w * 2, (h or w) * 2))  # fast JPEG pre-shrink, still >= 2x target
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    # Width alone bounds a cover-cropped photo; width+height bounds a contained one.
    scale = min(1.0, w / im.width, (h / im.height) if h else 1.0)
    if scale < 1.0:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.LANCZOS)

    out = io.BytesIO()
    im.save(out, "JPEG", quality=90 if la else 85, optimize=True, progressive=True,
            subsampling=0 if la else 2, icc_profile=icc)
    return out.getvalue()
