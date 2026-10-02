"""Community Settings (brand series) and the general marketing assets on the home page."""
import os
import unittest
from unittest import mock

os.environ.pop("DATABASE_URL", None)
for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
    os.environ.pop(k, None)

from app import bdx, db, series, store  # noqa: E402
from tests import test_auth  # noqa: E402


class Base(unittest.TestCase):
    setUp = test_auth.AuthTest.setUp
    tearDown = test_auth.AuthTest.tearDown
    client = test_auth.AuthTest.client
    make = test_auth.AuthTest.make

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class SeriesTest(Base):
    def tearDown(self):
        super().tearDown()
        bdx.SERIES_OVERRIDES = {}

    def test_marketing_director_sets_signature(self):
        mkt = self.make("mkt@test.local", "marketing")
        page = mkt.get("/c/fixture-park/settings").text
        self.assertIn("Community settings", page)
        self.assertIn("Sandlin Signature", page)
        self.assertEqual(mkt.get("/c/fixture-park/edit", follow_redirects=False).headers["location"], "/c/fixture-park/settings")
        mkt.post("/c/fixture-park/settings/community", data={"series": "signature"})
        self.assertIn("logo-signature.png", mkt.get("/c/fixture-park/plans").text)
        self.assertIn('class="tier-signature"', mkt.get("/c/fixture-park/info").text)
        series.refresh()   # survives a restart: read back from the database
        self.assertEqual(bdx.parse(b"""<Builders><Builder><Subdivision><SubdivisionNumber>SUB1</SubdivisionNumber>
            <SubdivisionName>Fixture Park</SubdivisionName></Subdivision></Builder></Builders>""")[0].tier, "signature")
        mkt.post("/c/fixture-park/settings/community", data={"series": "bogus"})   # unknown -> the default
        self.assertNotIn("logo-signature.png", mkt.get("/c/fixture-park/plans").text)

    def test_csm_cannot_open_settings(self):
        csm = self.make("csm@test.local", "csm")
        self.assertEqual(csm.get("/c/fixture-park/settings").status_code, 403)
        self.assertEqual(csm.post("/c/fixture-park/settings/community", data={"series": "signature"}).status_code, 403)


class AssetsTest(Base):
    def setUp(self):
        super().setUp()
        store._assets = None

    def test_upload_view_download_delete(self):
        mkt = self.make("mkt@test.local", "marketing")
        r = mkt.post("/assets", data={"title": "Fall Promo", "category": "Promotions"},
                     files={"file": ("fall promo.pdf", PDF, "application/pdf")}, follow_redirects=False)
        self.assertIn("msg=", r.headers["location"])
        home = mkt.get("/").text
        self.assertIn("Marketing Assets", home)
        self.assertLess(home.index("Marketing Assets"), home.index("Community Collateral"))
        self.assertIn("Fall Promo", home)
        csm = self.make("csm@test.local", "csm")
        self.assertIn("Fall Promo", csm.get("/").text)
        self.assertNotIn("Upload an asset", csm.get("/").text)
        with db.connect() as c:
            a = c.one("SELECT * FROM assets")
        got = csm.get(f"/assets/{a['id']}/x")
        self.assertEqual(got.content, PDF)
        self.assertEqual(got.headers["content-type"], "application/pdf")
        self.assertTrue(got.headers["content-disposition"].startswith("inline"))
        self.assertTrue(csm.get(f"/assets/{a['id']}/x?download=1").headers["content-disposition"].startswith("attachment"))
        self.assertEqual(self.client().get(f"/assets/{a['id']}/x", follow_redirects=False).status_code, 303)  # signed out
        self.assertEqual(csm.post(f"/assets/{a['id']}/delete").status_code, 403)
        mkt.post(f"/assets/{a['id']}/delete")
        self.assertNotIn("Fall Promo", mkt.get("/").text)
        self.assertEqual(csm.get(f"/assets/{a['id']}/x").status_code, 404)

    def test_rejects_html_and_csm_uploads(self):
        mkt = self.make("mkt@test.local", "marketing")
        r = mkt.post("/assets", data={"category": "Other"}, files={"file": ("x.html", b"<script>", "text/html")},
                     follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        csm = self.make("csm@test.local", "csm")
        self.assertEqual(csm.post("/assets", data={}, files={"file": ("a.pdf", PDF, "application/pdf")}).status_code, 403)
        with db.connect() as c:
            self.assertEqual(c.one("SELECT COUNT(*) AS n FROM assets")["n"], 0)


if __name__ == "__main__":
    unittest.main()
