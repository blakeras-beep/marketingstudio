"""Where resized photos are kept: Cloudflare R2, or memory when R2 isn't configured.

Nothing is written to local disk. The stored objects are a cache: losing them
only means each photo is resized again the next time a flyer asks for it.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict

log = logging.getLogger("marketingstudio")


def _env(name: str, default: str = "") -> str:
    # Pasted values often carry stray whitespace, newlines or quotes.
    return os.environ.get(name, default).strip().strip("'\"").strip()


def account_id(raw: str) -> str:
    """Accept the bare account ID or the S3 endpoint URL Cloudflare shows beside it."""
    m = re.search(r"([0-9a-f]{32})", raw.lower())
    return m.group(1) if m else raw


R2_ACCOUNT_ID = account_id(_env("R2_ACCOUNT_ID"))
R2_ACCESS_KEY_ID = _env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _env("R2_SECRET_ACCESS_KEY")
R2_BUCKET = _env("R2_BUCKET")
R2_PREFIX = _env("R2_PREFIX", "marketing-studio/img/")
# Optional public base URL for the bucket (custom domain or r2.dev). When set,
# /img redirects there and R2 serves the bytes; otherwise the app streams them.
R2_PUBLIC_URL = _env("R2_PUBLIC_URL").rstrip("/")
MEMORY_MB = int(os.environ.get("IMAGE_MEMORY_MB", "256"))


class MemoryStore:
    """Byte-capped LRU. Used for local dev and whenever R2 isn't configured."""

    name = "memory"

    def __init__(self, cap_bytes: int):
        self.cap, self.size = cap_bytes, 0
        self._d: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            data = self._d.get(key)
            if data is not None:
                self._d.move_to_end(key)
            return data

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            if key in self._d:
                self.size -= len(self._d.pop(key))
            self._d[key] = data
            self.size += len(data)
            while self.size > self.cap and len(self._d) > 1:
                self.size -= len(self._d.popitem(last=False)[1])

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._d.pop(key, None)
            if data is not None:
                self.size -= len(data)

    def public_url(self, key: str) -> str | None:
        return None


class R2Store:
    name = "r2"

    def __init__(self, client=None, prefix: str | None = None):
        self.prefix = R2_PREFIX if prefix is None else prefix
        if client is None:
            import boto3  # only needed when R2 is configured
            from botocore.config import Config
            client = boto3.client(
                "s3",
                endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                region_name="auto",
                # boto3 >= 1.36 sends CRC checksum headers by default, which R2
                # has rejected; Cloudflare's docs say to send them only when required.
                config=Config(retries={"max_attempts": 3}, connect_timeout=5, read_timeout=20,
                              request_checksum_calculation="when_required",
                              response_checksum_validation="when_required"),
            )
        self.client = client
        self._known: set[str] = set()  # keys confirmed to exist, saves a HEAD per request

    def _key(self, key: str) -> str:
        return self.prefix + key

    def get(self, key: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=R2_BUCKET, Key=self._key(key))
        except self.client.exceptions.NoSuchKey:
            return None
        self._known.add(key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        if key in self._known:
            return True
        try:
            self.client.head_object(Bucket=R2_BUCKET, Key=self._key(key))
        except self.client.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        self._known.add(key)
        return True

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg",
            cache_control: str = "public, max-age=31536000, immutable") -> None:
        self.client.put_object(
            Bucket=R2_BUCKET, Key=self._key(key), Body=data, ContentType=content_type,
            CacheControl=cache_control,
        )
        self._known.add(key)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=R2_BUCKET, Key=self._key(key))
        self._known.discard(key)

    def public_url(self, key: str) -> str | None:
        return f"{R2_PUBLIC_URL}/{self._key(key)}" if R2_PUBLIC_URL else None


def r2_configured() -> bool:
    return all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET))


_store = None
_lock = threading.Lock()
setup_error: str | None = None  # why R2 couldn't be set up, shown on /healthz/images


def get_store():
    """R2 when configured; otherwise, or if R2 setup fails, the memory cache."""
    global _store, setup_error
    with _lock:
        if _store is None:
            if r2_configured():
                try:
                    _store = R2Store()
                except Exception as e:
                    setup_error = f"{type(e).__name__}: {e}"
                    log.exception("R2 setup failed; using in-memory image cache")
            if _store is None:
                _store = MemoryStore(MEMORY_MB * 1024 * 1024)
        return _store


# ---------------------------------------------------------------- uploaded marketing assets
# Unlike the photo cache these are originals: R2 under R2_ASSET_PREFIX. Without R2 they're kept in
# memory, i.e. lost on restart, and the home page says so to the people who upload.
R2_ASSET_PREFIX = _env("R2_ASSET_PREFIX", "marketing-studio/assets/")
_assets = None


def get_asset_store():
    global _assets
    with _lock:
        if _assets is None:
            if r2_configured():
                try:
                    _assets = R2Store(prefix=R2_ASSET_PREFIX)
                except Exception:
                    log.exception("R2 setup failed; uploaded assets kept in memory")
            if _assets is None:
                _assets = MemoryStore(1024 * 1024 * 1024)
        return _assets
