"""Accounts, roles and the inputs editor, end to end through the app (SQLite, no network)."""
import os
import unittest
from unittest import mock

os.environ.pop("DATABASE_URL", None)
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "admin-password-1"
os.environ["COOKIE_SECURE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, bdx, db, inputs, main  # noqa: E402

XML = b"""<Builders><Builder><BrandName>Sandlin Homes</BrandName>
<Subdivision Status="Active"><SubdivisionNumber>SUB1</SubdivisionNumber><SubdivisionName>Fixture Park</SubdivisionName>
 <Plan><PlanName>Alpha</PlanName><BasePrice>300000</BasePrice><Bedrooms>3</Bedrooms><Baths>2</Baths>
  <Spec><SpecNumber>S1</SpecNumber><SpecAddress><SpecStreet1>1 Test Way</SpecStreet1></SpecAddress><SpecPrice>310000</SpecPrice></Spec>
 </Plan></Subdivision></Builder></Builders>"""


def _state():
    return bdx.FeedState(communities=bdx.parse(XML), fetched_at=bdx.datetime.now(bdx.timezone.utc))


class AuthTest(unittest.TestCase):
    def setUp(self):
        db.reset_for_tests()
        auth.bootstrap_admin()
        self.p = mock.patch.object(bdx, "state", lambda force=False: _state())
        self.p.start()
        self.app = TestClient(main.app, base_url="http://testserver")

    def tearDown(self):
        self.p.stop()

    def client(self, email=None, password=None):
        c = TestClient(main.app, base_url="http://testserver")
        if email:
            r = c.post("/login", data={"email": email, "password": password, "next": "/"}, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(r.headers["location"], "/")
        return c

    def make(self, email, role):
        auth.create_user(email, email.split("@")[0], role, "temp-password-1", None)
        return self.client(email, "temp-password-1")

    def test_everything_requires_sign_in(self):
        anon = self.client()
        r = anon.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))
        self.assertEqual(anon.get("/api/communities").status_code, 401)
        self.assertEqual(anon.get("/img?u=x&w=100").status_code, 401)
        self.assertEqual(anon.get("/livez").status_code, 200)
        self.assertEqual(anon.get("/login").status_code, 200)

    def test_wrong_password_and_inactive_user(self):
        anon = self.client()
        r = anon.post("/login", data={"email": "admin@test.local", "password": "nope"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        u = auth.create_user("gone@test.local", "Gone", "csm", "temp-password-1", None)
        auth.update_user(u["id"], active=False)
        r = anon.post("/login", data={"email": "gone@test.local", "password": "temp-password-1"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])

    def test_csm_views_but_cannot_edit_or_admin(self):
        csm = self.make("csm@test.local", "csm")
        self.assertEqual(csm.get("/").status_code, 200)
        self.assertEqual(csm.get("/c/fixture-park").status_code, 200)
        self.assertNotIn("Edit flyer inputs", csm.get("/c/fixture-park").text)
        self.assertEqual(csm.get("/c/fixture-park/edit").status_code, 403)
        self.assertEqual(csm.post("/c/fixture-park/edit/community", data={"lot_size": "x"}).status_code, 403)
        self.assertEqual(csm.post("/c/fixture-park/homes/1-test-way/edit", data={"features": "x"}).status_code, 403)
        self.assertNotIn("Edit bullets", csm.get("/c/fixture-park/homes/1-test-way").text)
        self.assertEqual(csm.get("/admin/users").status_code, 403)
        self.assertEqual(csm.post("/admin/users", data={}).status_code, 403)

    def test_marketing_edits_inputs_but_not_users(self):
        mkt = self.make("mkt@test.local", "marketing")
        self.assertIn("Edit flyer inputs", mkt.get("/c/fixture-park").text)
        self.assertEqual(mkt.get("/c/fixture-park/edit").status_code, 200)
        r = mkt.post("/c/fixture-park/edit/community",
                     data={"lot_size": " Standard ", "utilities": "Water: City\n\n Gas: Atmos "}, follow_redirects=False)
        self.assertIn("Saved+2+changes", r.headers["location"].replace("%20", "+").replace(" ", "+"))
        vals = inputs.load()
        self.assertEqual(vals.get("community", "SUB1", "lot_size"), "Standard")
        self.assertEqual(vals.lines("community", "SUB1", "utilities"), ["Water: City", "Gas: Atmos"])
        mkt.post("/c/fixture-park/edit/plans", data={"0.beds_range": "3 - 4"})
        self.assertEqual(inputs.load().get("plan", "SUB1|Alpha", "beds_range"), "3 - 4")
        # a home is edited from its own flyer, and the save lands back on it
        flyer = mkt.get("/c/fixture-park/homes/1-test-way").text
        self.assertIn("Edit bullets", flyer)
        self.assertIn('action="/c/fixture-park/homes/1-test-way/edit"', flyer)
        r = mkt.post("/c/fixture-park/homes/1-test-way/edit", data={"features": "Island\nPatio"}, follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/c/fixture-park/homes/1-test-way?msg="))
        self.assertEqual(inputs.load().lines("home", "S1", "features"), ["Island", "Patio"])
        self.assertIn("Saved.", mkt.get(r.headers["location"]).text)
        self.assertNotIn("Hacked", mkt.get("/c/fixture-park/homes/1-test-way?msg=Hacked").text)   # only our own notes show
        mkt.post("/c/fixture-park/edit/community", data={"lot_size": ""})   # blank clears
        self.assertIsNone(inputs.load().get("community", "SUB1", "lot_size"))
        self.assertEqual(mkt.get("/admin/users").status_code, 403)

    def test_admin_manages_users(self):
        admin = self.client("admin@test.local", "admin-password-1")
        r = admin.post("/admin/users", data={"name": "Casey", "email": "Casey@Test.local", "role": "csm",
                                             "password": "temp-password-1"}, follow_redirects=False)
        self.assertIn("msg=", r.headers["location"])
        casey = next(u for u in auth.list_users() if u["email"] == "casey@test.local")
        self.assertEqual(casey["role"], "csm")
        admin.post(f"/admin/users/{casey['id']}", data={"role": "marketing"})
        self.assertEqual(auth.get_user(casey["id"])["role"], "marketing")
        r = admin.post("/admin/users", data={"name": "Dup", "email": "casey@test.local", "role": "csm",
                                             "password": "temp-password-1"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        r = admin.post("/admin/users", data={"name": "Short", "email": "s@test.local", "role": "csm",
                                             "password": "short"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])

    def test_last_admin_cannot_be_demoted_or_deactivated(self):
        admin = self.client("admin@test.local", "admin-password-1")
        me = next(u for u in auth.list_users() if u["role"] == "admin")
        r = admin.post(f"/admin/users/{me['id']}", data={"role": "csm"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        r = admin.post(f"/admin/users/{me['id']}", data={"active": "0"}, follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        self.assertEqual(auth.get_user(me["id"])["role"], "admin")

    def test_deactivating_or_resetting_signs_user_out(self):
        csm = self.make("csm2@test.local", "csm")
        uid = next(u for u in auth.list_users() if u["email"] == "csm2@test.local")["id"]
        admin = self.client("admin@test.local", "admin-password-1")
        admin.post(f"/admin/users/{uid}/password", data={"password": "another-password-1"})
        self.assertEqual(csm.get("/", follow_redirects=False).status_code, 303)
        csm = self.client("csm2@test.local", "another-password-1")
        admin.post(f"/admin/users/{uid}", data={"active": "0"})
        self.assertEqual(csm.get("/", follow_redirects=False).status_code, 303)

    def test_change_own_password(self):
        csm = self.make("csm3@test.local", "csm")
        r = csm.post("/account/password", data={"current": "wrong", "new": "new-password-12", "confirm": "new-password-12"},
                     follow_redirects=False)
        self.assertIn("err=", r.headers["location"])
        r = csm.post("/account/password", data={"current": "temp-password-1", "new": "new-password-12",
                                                "confirm": "new-password-12"}, follow_redirects=False)
        self.assertIn("msg=", r.headers["location"])
        self.assertEqual(csm.get("/", follow_redirects=False).status_code, 200)   # still signed in here
        self.client("csm3@test.local", "new-password-12")

    def test_cross_site_post_rejected(self):
        admin = self.client("admin@test.local", "admin-password-1")
        r = admin.post("/admin/users", data={"name": "X", "email": "x@test.local", "role": "csm",
                                             "password": "temp-password-1"},
                       headers={"Origin": "https://evil.example"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_open_redirect_blocked(self):
        anon = self.client()
        r = anon.post("/login", data={"email": "admin@test.local", "password": "admin-password-1",
                                      "next": "//evil.example"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/")


class PasswordTest(unittest.TestCase):
    def test_hash_roundtrip(self):
        h = auth.hash_password("correct horse")
        self.assertTrue(auth.verify_password("correct horse", h))
        self.assertFalse(auth.verify_password("wrong", h))
        self.assertFalse(auth.verify_password("x", "garbage"))


if __name__ == "__main__":
    unittest.main()


class SeedImportTest(unittest.TestCase):
    """The reference import fills only empty fields and matches names loosely."""

    def setUp(self):
        db.reset_for_tests()
        auth.bootstrap_admin()
        xml = XML.replace(b"<SubdivisionName>Fixture Park</SubdivisionName>",
                          b"<SubdivisionName>Fixture Park</SubdivisionName>").replace(
            b"<PlanName>Alpha</PlanName>", b"<PlanName>Alpha II</PlanName>")
        self.p = mock.patch.object(bdx, "state", lambda force=False: bdx.FeedState(communities=bdx.parse(xml)))
        self.p.start()
        from app import seed
        self.seed = seed
        self.data = {
            "communities": {"Fixture Park - Ph 2": {"hoa": "$900/year", "utilities": ["Gas: Atmos", "Water: City"]}},
            "plans": {"Fixture Park - Ph 2": {"ALPHA 2": {"beds_range": "3 - 4"}, "Gone Plan": {"beds_range": "1"}}},
            "homes": {"Fixture Park - Ph 2": {"1 Test Way": {"features": ["Island"]}, "9 Sold Ln": {"features": ["x"]}}},
        }

    def tearDown(self):
        self.p.stop()

    def run_with(self, data):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        with mock.patch.object(self.seed, "SEED", __import__("pathlib").Path(f.name)):
            return self.seed.run(1)

    def test_fills_empty_and_matches_loosely(self):
        inputs.save("community", "SUB1", {"hoa": "$1,000/year"}, 1)   # marketing already set this
        r = self.run_with(self.data)
        v = inputs.load()
        self.assertEqual(v.get("community", "SUB1", "hoa"), "$1,000/year")          # kept
        self.assertEqual(v.lines("community", "SUB1", "utilities"), ["Gas: Atmos", "Water: City"])
        self.assertEqual(v.get("plan", "SUB1|Alpha II", "beds_range"), "3 - 4")     # ALPHA 2 == Alpha II
        self.assertEqual(v.lines("home", "S1", "features"), ["Island"])
        self.assertIn("plan Gone Plan (Fixture Park)", r["unmatched"])
        self.assertEqual(self.run_with(self.data)["fields"], 0)                      # idempotent

    def test_better_read_replaces_untouched_import_only(self):
        old = {**self.data, "communities": {"Fixture Park - Ph 2": {"standard_features": "# A\n- old read"}},
               "dated": {"Fixture Park - Ph 2": {"standard_features": "2026-08-04"}}}
        self.run_with(old)
        self.assertEqual(inputs.last_changed("community", ["SUB1"], "standard_features"), "08/04/26")   # the sheet's date
        new = {**old, "communities": {"Fixture Park - Ph 2": {"standard_features": "# A\n- better read"}}}
        self.run_with(new)
        self.assertEqual(inputs.load().get("community", "SUB1", "standard_features"), "# A\n- better read")
        inputs.save("community", "SUB1", {"standard_features": "# A\n- marketing's text"}, 1)          # edited in the Studio
        self.run_with({**new, "communities": {"Fixture Park - Ph 2": {"standard_features": "# A\n- newest"}}})
        self.assertEqual(inputs.load().get("community", "SUB1", "standard_features"), "# A\n- marketing's text")

    def test_real_seed_file_is_valid(self):
        import json
        data = json.loads(self.seed.SEED.read_text(encoding="utf-8"))
        self.assertTrue(data["communities"] and data["plans"] and data["homes"])
        cl = data["communities"]["Country Lakes"]
        self.assertEqual(cl["hoa"], "$900/year")
        self.assertIn("- Upgraded **Kichler** light package", cl["standard_features"])
