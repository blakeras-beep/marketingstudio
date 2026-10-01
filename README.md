# Sandlin Marketing Studio

Community-level marketing collateral that updates itself from the BDX XML feed.
Marketing owns the layouts; the feed supplies every fact. Each flyer is rendered
from live data when it's opened, so a flyer printed next month shows next
month's prices and inventory.

**House rules** (from the collateral context doc): facts come only from the feed,
a missing value prints `—`, a missing photo shows placeholder art, nothing is
invented. Every piece carries the Equal Housing Opportunity mark. US Letter only.

## Accounts and roles

Everything requires signing in. Roles (enforced server-side on every route):

| Role | Can |
|---|---|
| Admin | create and manage users (`/admin/users`), edit everything |
| Marketing | edit flyer inputs (`/c/{community}/edit`) |
| CSM | view and download flyers |

The first admin comes from `ADMIN_EMAIL` / `ADMIN_PASSWORD` (also restores access if no
active admin is left). There is always at least one active admin. Users change their own
password at `/account`; an admin reset signs the user out everywhere.

Storage: Postgres via `DATABASE_URL` (Railway: add a PostgreSQL database and reference its
`DATABASE_URL` from this service). Without it the app uses an in-memory SQLite database,
which is only for local dev (`DATABASE_URL=sqlite:///studio.db` keeps it on disk).

## Flyer inputs (marketing)

Facts neither the BDX feed nor Blueprint carries, edited per community at
`/c/{community}/edit`: flyer description override, hero and amenity photo picks (from the
community's feed photos only), lot size, HOA / tax rate fallbacks, utility providers,
attractions, plan bed/bath ranges and per-home feature bullets. Every change is kept in
`input_history`. **Temporary home**: these are meant to move upstream (Big Board /
Blueprint) later; the field list lives in one place (`app/inputs.py`).

## Design source

Each flyer reproduces a current Sandlin original from `reference/flyers/` (Canva and Excel
PDFs), positioned in points on the original's coordinates. Fonts: Cormorant Garamond,
Poppins and EB Garamond as in the Canva originals; Carlito (metric-compatible with Calibri)
for the Excel sheets. Logos, Equal Housing marks and grid icons were extracted from the
originals (`app/static/brand/`).

## What's in the repository

Open `/`, pick a community, and preview or download any flyer:

| Flyer | Route | Pages |
|---|---|---|
| Community Info Sheet | `/c/{community}/info` | 1 |
| Inventory List | `/c/{community}/pricing` | 15 homes per page |
| Price Sheet (available floor plans) | `/c/{community}/plans` | 12 plans per page |
| Photo Inventory | `/c/{community}/grid` | 10 photo cards per page, membership always live |
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

### Blueprint (Big Board)

What the website feed doesn't carry comes from Blueprint's read-only
`GET /api/feed/marketing` (`app/blueprint.py`), cached for `BLUEPRINT_CACHE_TTL`
seconds, last good copy kept on failure:

- **SOLD**: a home Blueprint has Under Contract. When the website drops a sold
  home, its last feed copy (`homes_seen` table) is put back so it keeps the SOLD
  stamp until it settles and leaves Blueprint.
- **Was-price**: Blueprint's retail price, shown only when above the price the
  flyer prints.
- **HOA and tax rate**: `community_rules`. These override marketing's HOA and
  tax inputs; the edit page shows Blueprint's value when there is one.

Homes match on community name + street address (abbreviations normalised).
Unset `BLUEPRINT_URL` / `MARKETING_FEED_TOKEN` and those lines are simply left off.

JSON for debugging or for other tools: `GET /api/communities`,
`GET /api/communities/{community}`, `GET /healthz`.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload            # http://localhost:8000
pip install -r requirements-dev.txt
python -m unittest                       # parser, format, image and R2 tests (R2 is stubbed)
```

Settings (environment variables): `BDX_FEED_URL`, `BDX_CACHE_TTL` (default 900 s),
`BDX_TIMEOUT` (20 s), `DISCLAIMER` (footer text), `SIGNATURE_COMMUNITIES`
(comma-separated community names), `BLUEPRINT_URL` + `MARKETING_FEED_TOKEN`
(the same token as on Blueprint), `BLUEPRINT_CACHE_TTL` (900 s).

Deploys anywhere that runs a Python web process. Railway: `railway.json` sets
the start command and a `/livez` health check (independent of the feed, so a
BDX outage can't block a deploy; `/healthz` reports feed and image-store state).
`Procfile` covers Heroku-style hosts.

## Photos

Feed photos are often photographer originals (up to ~6000 px and 9–12 MB),
which the browser would embed untouched, making a two-page flyer up to 30 MB.
`/img` (`app/images.py`, Pillow) fetches each photo once, downsamples it to the
size it prints at (200 dpi for photos, 300 dpi for floor plans) and keeps its
color profile. A JPEG already at or under print size is served byte-for-byte.
Only the feed's hosts are fetched (S3 `buildercloud`, `www.sandlinhomes.com`).

Resized photos are stored in **Cloudflare R2** (`app/store.py`), never on local
disk. They're a cache: deleting them only means each photo is resized again the
next time a flyer needs it. Without R2 settings (local dev) the app keeps them
in a byte-capped in-memory cache instead. `/healthz` reports which is active.
The store is best-effort: if R2 errors (bad key, wrong bucket), photos are still
resized and served directly and the error is logged. `/healthz/images` runs the
pipeline step by step (feed photo fetch and resize, store write, store read)
and prints the exact error at whichever step fails.

Measured on the live feed: single-home flyers median 572 KB (max 1.1 MB),
grid flyers median 645 KB (max 1.2 MB), info sheets ~500 KB.

| Setting | |
|---|---|
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | all four enable R2 (an API token with Object Read & Write on the bucket) |
| `R2_PREFIX` | key prefix, default `marketing-studio/img/` |
| `R2_PUBLIC_URL` | optional custom domain or r2.dev URL for the bucket; when set, `/img` redirects there and R2 serves the bytes, otherwise the app streams them |
| `IMAGE_DPI` | photo print resolution, default 200 |
| `IMAGE_MEMORY_MB` | in-memory cache cap when R2 isn't configured, default 256 |

## Print-readiness contract

Each flyer sets `<html data-print-ready="true">` once fonts are loaded and every
image has settled (12 s failsafe). A server-side renderer (Playwright) can wait
on that attribute to produce PDFs without any frontend change. Fonts are
self-hosted so PDFs never fall back to a system font. Any sheet whose content
runs past its edge is outlined in red on screen with a warning banner.
