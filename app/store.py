"""Where resized photos are kept: Cloudflare R2, or memory when R2 isn't configured.

Nothing is written to local disk. The stored objects are a cache: losing them
only means each photo is resized again the next time a flyer asks for it.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")
R2_PREFIX = os.environ.get("R2_PREFIX", "marketing-studio/img/")
# Optional public base URL for the bucket (custom domain or r2.dev). When set,
# /img redirects there and R2 serves the bytes; otherwise the app streams them.
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
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

    def public_url(self, key: str) -> str | None:
        return None


class R2Store:
    name = "r2"

    def __init__(self, client=None):
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
        return R2_PREFIX + key

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

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=R2_BUCKET, Key=self._key(key), Body=data, ContentType="image/jpeg",
            CacheControl="public, max-age=31536000, immutable",
        )
        self._known.add(key)

    def public_url(self, key: str) -> str | None:
        return f"{R2_PUBLIC_URL}/{self._key(key)}" if R2_PUBLIC_URL else None


def r2_configured() -> bool:
    return all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET))


_store = None
_lock = threading.Lock()


def get_store():
    global _store
    with _lock:
        if _store is None:
            _store = R2Store() if r2_configured() else MemoryStore(MEMORY_MB * 1024 * 1024)
        return _store
