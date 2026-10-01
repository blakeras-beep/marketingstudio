"""Blueprint lookups (was-price, SOLD, HOA, tax) and keeping SOLD homes after the website drops them."""
import os
import unittest

os.environ.pop("DATABASE_URL", None)

from app import bdx, blueprint, db, homes_seen
from tests.test_bdx import XML


def fixture(cs):
    return next(c for c in cs if c.name == "Fixture Community")

BODY = {
    "homes": [
        {"community": "Fixture Community", "address1": "1 First Street", "status": "Under Contract", "sold": True, "was_price": None},
        {"community": "Fixture Community", "address1": "2 Second St", "status": "Available", "sold": False, "was_price": 999999.0},
        {"community": "Elsewhere", "address1": "9 Gone Lane", "status": "Under Contract", "sold": True, "was_price": None},
    ],
    "communities": [{"developmentcode": "FX", "community": "Fixture Community", "tax_rate": 0.01909, "hoa_quarterly": 225.0, "hoa_annual": 900.0}],
}


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.c = fixture(bdx.parse(XML))
        self.bp = blueprint.Blueprint(BODY)

    def home(self, address, price=100000):
        return bdx.Home(id="x", slug="x", address=address, plan=None, city=None, state=None, zip=None, price=price,
                        sqft=None, beds=None, baths=None, half_baths=None, garage=None, stories=None, move_in=None,
                        description=None, hero=None)

    def test_sold_matches_abbreviated_address(self):
        self.assertTrue(self.bp.is_sold(self.c, self.home("1 First St")))
        self.assertFalse(self.bp.is_sold(self.c, self.home("2 Second Street")))
        self.assertFalse(self.bp.is_sold(self.c, self.home("3 Unknown Rd")))

    def test_was_price_only_above_the_printed_price(self):
        self.assertEqual(self.bp.was_price(self.c, self.home("2 Second St", 450000)), 999999.0)
        self.assertIsNone(self.bp.was_price(self.c, self.home("2 Second St", 1200000)))
        self.assertIsNone(self.bp.was_price(self.c, self.home("2 Second St", None)))

    def test_hoa_and_tax(self):
        self.assertEqual(self.bp.hoa(self.c), "$900/year")
        self.assertEqual(self.bp.tax_rate(self.c), "1.909%")

    def test_unconfigured_is_empty(self):
        for k in ("BLUEPRINT_URL", "MARKETING_FEED_TOKEN"):
            os.environ.pop(k, None)
        bp = blueprint.current()
        self.assertIsNone(bp.hoa(self.c))
        self.assertFalse(bp.is_sold(self.c, self.home("1 First St")))


class SoldHomesStayTest(unittest.TestCase):
    def setUp(self):
        db.reset_for_tests()

    def test_sold_home_returns_after_leaving_the_feed(self):
        first = bdx.parse(XML)
        homes_seen.remember(first)
        sold_addr = next(h for h in fixture(first).homes if h.address == "1 First St")
        later = bdx.parse(XML)
        fixture(later).homes = [h for h in fixture(later).homes if h.id != sold_addr.id]   # the website dropped it
        n = homes_seen.restore_sold(later, blueprint.Blueprint(BODY))
        self.assertEqual(n, 1)
        self.assertIn("1 First St", [h.address for h in fixture(later).homes])
        # not sold (or closed: gone from Blueprint) -> not restored
        again = bdx.parse(XML)
        fixture(again).homes = [h for h in fixture(again).homes if h.id != sold_addr.id]
        self.assertEqual(homes_seen.restore_sold(again, blueprint.Blueprint({"homes": [], "communities": []})), 0)

    def test_listed_home_is_not_duplicated(self):
        cs = bdx.parse(XML)
        homes_seen.remember(cs)
        before = len(fixture(cs).homes)
        self.assertEqual(homes_seen.restore_sold(cs, blueprint.Blueprint(BODY)), 0)
        self.assertEqual(len(fixture(cs).homes), before)


if __name__ == "__main__":
    unittest.main()
