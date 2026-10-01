import unittest

from app import inputs


class ParseFeaturesTest(unittest.TestCase):
    def test_sections_bold_and_page_break(self):
        s = inputs.parse_features("# Interior\n- Upgraded **Kichler** light package\n===\n# Exterior\n- Sodded yard")
        self.assertEqual([x["title"] for x in s], ["Interior", "", "Exterior"])
        self.assertEqual(s[0]["items"][0], [("Upgraded ", False), ("Kichler", True), (" light package", False)])
        self.assertTrue(s[1].get("page_break"))
        self.assertFalse(s[2].get("page_break"))

    def test_empty(self):
        self.assertEqual(inputs.parse_features(""), [])
        self.assertEqual(inputs.parse_features(None), [])


class ParseElevationsTest(unittest.TestCase):
    def test_rows(self):
        rows = inputs.parse_elevations("A | 2,167 | 469,900\nB | 2,167 | \n")
        self.assertEqual(rows[0]["elevation"], "A")
        self.assertEqual(rows[0]["sqft"], 2167)
        self.assertEqual(rows[0]["price"], 469900)
        self.assertIsNone(rows[1]["price"])   # missing stays missing: printed as a dash


if __name__ == "__main__":
    unittest.main()
