"""Image proxy: resize math, passthrough and color-profile handling (no network)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app import images


def _jpeg(w, h, icc=None):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, "JPEG", icc_profile=icc)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ImagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(images, "CACHE_DIR", Path(self.tmp.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _render(self, data, *args, **kw):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(data)):
            return images.render("https://s3.amazonaws.com/buildercloud/x.jpeg", *args, **kw)

    def test_allowlist(self):
        self.assertTrue(images.allowed("https://s3.amazonaws.com/buildercloud/a.jpeg"))
        self.assertFalse(images.allowed("https://s3.amazonaws.com/other-bucket/a.jpeg"))
        self.assertFalse(images.allowed("https://evil.example/a.jpeg"))
        self.assertEqual(images.src("https://evil.example/a.jpg", 2), "https://evil.example/a.jpg")
        self.assertIsNone(images.src(None, 2))
        self.assertIn("w=1700", images.src("https://s3.amazonaws.com/buildercloud/a.jpeg", 8.5))

    def test_downsamples_and_keeps_icc(self):
        icc = b"\x00" * 128  # opaque profile bytes are carried, not interpreted
        out = Image.open(self._render(_jpeg(4000, 3000, icc), 1000))
        self.assertEqual(out.size, (1000, 750))
        self.assertEqual(out.info.get("icc_profile"), icc)

    def test_fit_within_box(self):
        out = Image.open(self._render(_jpeg(3000, 4000), 1500, 1000))
        self.assertEqual(out.size, (750, 1000))

    def test_small_jpeg_passes_through_untouched(self):
        data = _jpeg(800, 600)
        self.assertEqual(self._render(data, 1700).read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
