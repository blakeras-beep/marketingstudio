"""Runtime settings, all overridable by environment variable."""
import os

BDX_FEED_URL = os.environ.get(
    "BDX_FEED_URL", "https://feed.mybuildercloud.com/bdx/26016593903.xml"
)
# Seconds a parsed feed is reused before refetching. On fetch failure the last
# good parse keeps serving (and the UI says how stale it is).
BDX_CACHE_TTL = int(os.environ.get("BDX_CACHE_TTL", "900"))
BDX_TIMEOUT = float(os.environ.get("BDX_TIMEOUT", "20"))


# Plain editable copy, not a binding: it carries no feed facts.
DISCLAIMER = os.environ.get(
    "DISCLAIMER",
    "Prices, plans, specifications, availability and features are subject to change "
    "without notice. Square footages are approximate. Photography and renderings may "
    "show options, upgrades or landscaping not included in the base price. See a "
    "Sandlin Homes sales counselor for complete details.",
)
