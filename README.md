# Sandlin Marketing Studio

Community-level marketing collateral that updates itself from the BDX XML feed.
Marketing owns the layouts; the feed supplies every fact. Each flyer is rendered
from live data when it's opened, so a flyer printed next month shows next
month's prices and inventory.

**House rules** (from the collateral context doc): facts come only from the feed,
a missing value prints `—`, a missing photo shows placeholder art, nothing is
invented. Every piece carries the Equal Housing Opportunity mark. US Letter only.

## What's in the repository

Open `/`, pick a community, and preview or download any flyer:

| Flyer | Route | Pages |
|---|---|---|
| Community Info Sheet | `/c/{community}/info` | 1 |
| Inventory Pricing Sheet | `/c/{community}/pricing` | paginates (25 rows, then 29 per page) |
| Floor Plans Sheet | `/c/{community}/plans` | paginates (25, then 29) |
| Inventory Grid Flyer | `/c/{community}/grid` | 6 photo cards per page, membership always live |
| Single-Home Flyer | `/c/{community}/homes/{home}` | 2 (photo + facts, floor plan) |

Add `?print=1` to any flyer URL to open the print dialog as soon as it's ready
(Save as PDF, Letter, margins none). The PDF filename defaults to
`Sandlin-<Community>-<Flyer>-<YYYY-MM-DD>`.

A flyer is listed as unavailable (with the reason) when the feed has nothing
for it, e.g. no homes means no pricing sheet.

## Brand

Tokens and rules follow the Sandlin DESIGN.md: Cormorant Garamond display
(uppercase, wide-tracked, lining figures), Poppins for everything else, navy
`#002147` structure, 2px navy framing, square photo frames, fonts self-hosted.

Two tiers, never mixed in one piece:

| | Sandlin Homes | Sandlin Signature |
|---|---|---|
| Logo | silver arch (`logo-navy.png` / `logo-white.png` on navy) | gold arch (`logo-signature.png`) |
| Accent | slate-blue `#8B9EB7` | champagne `#E4CB84` |
| "Ready Now" tag | lime `#E0F19C` ribbon | navy with champagne text (no ribbons) |

A community is Signature when its BDX `<Builder><BrandName>` contains
"Signature", or when it's named in `SIGNATURE_COMMUNITIES`. There is no reversed
(white) Signature logo yet, so on navy it sits on a white plate; drop a
`logo-signature-white.png` in `app/static/` and swap it in
`templates/partials/logo.html` when one exists.

## Data

`app/bdx.py` fetches `BDX_FEED_URL`, parses it, and caches the result for
`BDX_CACHE_TTL` seconds. If a refresh fails, the last good parse keeps serving
and every page says how old it is. `POST /api/refresh` forces a refetch.

| BDX | Used for |
|---|---|
| `Subdivision/SubdivisionName`, `@Status`, `SubAddress/*` | community name, status, city line |
| `SubDescription`, `DrivingDirections`, `SubImage` | about copy, directions, hero photo |
| `Schools` (`DistrictName`, `School[@Type]/SchoolName` or `Elementary`/`Middle`/`High`) | schools |
| `SalesOffice/Agent`, `Phone/AreaCode+Prefix+Suffix`, `Email`, `Hours` | contact |
| `Plan/PlanName`, `BasePrice`, `BaseSqft`, `Bedrooms`, `Baths`, `HalfBaths`, `Stories`, `Garage`, `PlanImages/*` | floor plans sheet |
| `Plan/Spec/SpecNumber`, `SpecAddress/SpecStreet1…`, `SpecPrice`, `SpecSqft`, `Spec*`, `SpecMoveInDate`, `SpecImages/*` | inventory, grid, single-home |

Derived values use feed numbers only: a plan's "priced from" is its `BasePrice`,
else its lowest available spec price; a home whose move-in date has passed
prints "Ready Now"; a spec with no photos of its own shows its plan's elevation
and floor plan. A price of 0 is treated as "not priced" and prints `—`.

JSON for debugging or for other tools: `GET /api/communities`,
`GET /api/communities/{community}`, `GET /healthz`.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload            # http://localhost:8000
python -m unittest                       # parser/format tests
```

Settings (environment variables): `BDX_FEED_URL`, `BDX_CACHE_TTL` (default 900 s),
`BDX_TIMEOUT` (20 s), `DISCLAIMER` (footer text), `SIGNATURE_COMMUNITIES`
(comma-separated community names).

Deploys anywhere that runs a Python web process (`Procfile` included for
Railway/Heroku-style hosts).

## Photos

Feed photos are often photographer originals (up to ~6000 px and 9–12 MB),
which the browser would embed untouched, making a two-page flyer up to 30 MB.
`/img` (`app/images.py`, Pillow) fetches each photo once, downsamples it to the
size it prints at (200 dpi for photos, 300 dpi for floor plans), keeps its color
profile, and caches it on disk. A JPEG already at or under print size is served
byte-for-byte. Only the feed's hosts are fetched (S3 `buildercloud`,
`www.sandlinhomes.com`).

Measured on the live feed: single-home flyers median 572 KB (max 1.1 MB),
grid flyers median 645 KB (max 1.2 MB), info sheets ~500 KB.

Settings: `IMAGE_DPI` (default 200), `IMAGE_CACHE_DIR` (default
`/tmp/marketingstudio-img`; safe to delete, it refills on demand).

## Print-readiness contract

Each flyer sets `<html data-print-ready="true">` once fonts are loaded and every
image has settled (12 s failsafe). A server-side renderer (Playwright) can wait
on that attribute to produce PDFs without any frontend change. Fonts are
self-hosted so PDFs never fall back to a system font. Any sheet whose content
runs past its edge is outlined in red on screen with a warning banner.
