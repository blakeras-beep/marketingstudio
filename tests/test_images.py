"""Image proxy and photo store (no network: urlopen is mocked, R2 is stubbed)."""
import io
import unittest
from unittest import mock

import boto3
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient
from PIL import Image

from app import images, main, store

SRC = "https://s3.amazonaws.com/buildercloud/x.jpeg"


def _jpeg(w, h, icc=None):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, "JPEG", icc_profile=icc)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fetch(data):
    return mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(data))


class RenderTest(unittest.TestCase):
    def _render(self, data, *args, **kw):
        with _fetch(data):
            return images.render(SRC, *args, **kw)

    def test_allowlist(self):
        self.assertTrue(images.allowed(SRC))
        self.assertFalse(images.allowed("https://s3.amazonaws.com/other-bucket/a.jpeg"))
        self.assertFalse(images.allowed("https://evil.example/a.jpeg"))
        self.assertEqual(images.src("https://evil.example/a.jpg", 2), "https://evil.example/a.jpg")
        self.assertIsNone(images.src(None, 2))
        self.assertIn("w=1700", images.src(SRC, 8.5))

    def test_downsamples_and_keeps_icc(self):
        icc = b"\x00" * 128  # opaque profile bytes are carried, not interpreted
        out = Image.open(io.BytesIO(self._render(_jpeg(4000, 3000, icc), 1000)))
        self.assertEqual(out.size, (1000, 750))
        self.assertEqual(out.info.get("icc_profile"), icc)

    def test_fit_within_box(self):
        out = Image.open(io.BytesIO(self._render(_jpeg(3000, 4000), 1500, 1000)))
        self.assertEqual(out.size, (750, 1000))

    def test_small_jpeg_passes_through_untouched(self):
        data = _jpeg(800, 600)
        self.assertEqual(self._render(data, 1700), data)


class MemoryStoreTest(unittest.TestCase):
    def test_lru_byte_cap(self):
        s = store.MemoryStore(cap_bytes=10)
        s.put("a", b"12345")
        s.put("b", b"12345")
        s.get("a")              # a is now most recent
        s.put("c", b"12345")    # evicts b
        self.assertEqual((s.get("a"), s.get("b"), s.get("c")), (b"12345", None, b"12345"))


def _r2(public=""):
    client = boto3.client("s3", region_name="auto", endpoint_url="https://acct.r2.cloudflarestorage.com",
                          aws_access_key_id="k", aws_secret_access_key="s")
    patches = [mock.patch.object(store, "R2_BUCKET", "bucket"),
               mock.patch.object(store, "R2_PUBLIC_URL", public)]
    for p in patches:
        p.start()
    return store.R2Store(client=client), Stubber(client), patches


class R2Test(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.url = "/img?u=" + SRC + "&w=1000"
        self.key = store.R2_PREFIX + images.cache_key(SRC, 1000, None, False)

    def _serve(self, r2, data):
        with mock.patch.object(store, "_store", r2), _fetch(data):
            return self.client.get(self.url, follow_redirects=False)

    def test_miss_renders_uploads_and_streams(self):
        r2, stub, patches = _r2()
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        stub.add_response("put_object", {}, {"Bucket": "bucket", "Key": self.key, "Body": ANY,
                                             "ContentType": "image/jpeg", "CacheControl": ANY})
        with stub:
            r = self._serve(r2, _jpeg(3000, 2000))
            stub.assert_no_pending_responses()
        [p.stop() for p in patches]
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(r.content)).size, (1000, 667))

    def test_hit_streams_from_r2_without_fetching_source(self):
        r2, stub, patches = _r2()
        stored = _jpeg(1000, 667)
        stub.add_response("get_object", {"Body": io.BytesIO(stored)},
                          {"Bucket": "bucket", "Key": self.key})
        with stub, mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            with mock.patch.object(store, "_store", r2):
                r = self.client.get(self.url)
        [p.stop() for p in patches]
        self.assertEqual(r.content, stored)

    def test_public_url_redirects(self):
        r2, stub, patches = _r2(public="https://img.example.com")
        stub.add_response("head_object", {}, {"Bucket": "bucket", "Key": self.key})
        with stub:
            r = self._serve(r2, b"")
        [p.stop() for p in patches]
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://img.example.com/" + self.key)

    def test_store_failure_shows_placeholder_path(self):
        r2, stub, patches = _r2()
        stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
        with stub:
            r = self._serve(r2, _jpeg(10, 10))
        [p.stop() for p in patches]
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
