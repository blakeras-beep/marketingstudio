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
        mkt.post("/c/fixture-park/edit/homes/1-test-way", data={"features": "Island\nPatio"})
        self.assertEqual(inputs.load().lines("home", "S1", "features"), ["Island", "Patio"])
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
