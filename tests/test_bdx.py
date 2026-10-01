"""Parser and formatting tests.

The XML here is a structural fixture (placeholder names, not Sandlin data);
it only exercises the BDX shapes the parser has to handle.
"""
import unittest
from datetime import date

from app import bdx, fmt
from app.flyers import paginate

XML = b"""<?xml version="1.0"?>
<Builders xmlns="http://example.invalid/bdx"><Corporation><Builder>
 <Subdivision Status="Active">
  <SubdivisionName>Fixture Community</SubdivisionName>
  <SalesOffice>
   <Agent>Agent A</Agent><Agent>Agent B</Agent>
   <Phone><AreaCode>000</AreaCode><Prefix>000</Prefix><Suffix>0000</Suffix></Phone>
   <Email>office@example.invalid</Email><Hours>Hours text</Hours>
  </SalesOffice>
  <SubAddress><SubStreet1>Street</SubStreet1><SubCity>City</SubCity><SubState>ST</SubState><SubZIP>00000</SubZIP></SubAddress>
  <Schools><DistrictName>District</DistrictName>
   <School Type="Elementary"><SchoolName>Elem</SchoolName></School>
   <School Type="High"><SchoolName>High</SchoolName></School>
  </Schools>
  <SubImage SequencePosition="2">https://img.invalid/2.jpg</SubImage>
  <SubImage SequencePosition="1">https://img.invalid/1.jpg</SubImage>
  <Plan Type="SingleFamily">
   <PlanName>Plan With Base</PlanName><BasePrice>100000</BasePrice><BaseSqft>1,500</BaseSqft>
   <Bedrooms excluded="0">3</Bedrooms><Baths>2</Baths><HalfBaths>1</HalfBaths>
   <PlanImages><ElevationImage>https://img.invalid/elev.jpg</ElevationImage>
    <FloorPlanImage>https://img.invalid/fp.jpg</FloorPlanImage></PlanImages>
   <Spec SpecNumber="A.1">
    <SpecAddress><SpecStreet1>1 First St</SpecStreet1><SpecCity>City</SpecCity></SpecAddress>
    <SpecPrice>0</SpecPrice>
    <SpecMoveInDate><Day>15</Day><Month>3</Month><Year>2027</Year></SpecMoveInDate>
   </Spec>
  </Plan>
  <Plan>
   <PlanName>Plan Priced By Spec</PlanName><BasePrice>0</BasePrice>
   <Spec><SpecAddress><SpecStreet1>2 Second St</SpecStreet1></SpecAddress><SpecPrice>$250,000</SpecPrice></Spec>
   <Spec><SpecAddress><SpecStreet1>3 Third St</SpecStreet1></SpecAddress><SpecPrice>200000</SpecPrice></Spec>
  </Plan>
  <Spec><SpecAddress><SpecStreet1>4 Orphan St</SpecStreet1></SpecAddress></Spec>
 </Subdivision>
 <Subdivision><SubdivisionName>Empty One</SubdivisionName></Subdivision>
</Builder></Corporation></Builders>"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.cs = bdx.parse(XML)
        self.c = self.cs[1]

    def test_communities_sorted_and_slugged(self):
        self.assertEqual([c.name for c in self.cs], ["Empty One", "Fixture Community"])
        self.assertEqual(self.c.slug, "fixture-community")
        self.assertEqual(self.c.status, "Active")

    def test_contact_and_address(self):
        self.assertEqual(self.c.contact.agents, ["Agent A", "Agent B"])
        self.assertEqual(self.c.contact.phone, "000.000.0000")
        self.assertEqual((self.c.city, self.c.state, self.c.zip), ("City", "ST", "00000"))

    def test_schools_type_attribute_form(self):
        s = self.c.schools
        self.assertEqual((s.district, s.elementary, s.middle, s.high), ("District", "Elem", None, "High"))

    def test_images_ordered_by_sequence(self):
        self.assertEqual(self.c.photos, ["https://img.invalid/1.jpg", "https://img.invalid/2.jpg"])

    def test_plan_fields_and_price_from(self):
        p1, p2 = self.c.plans
        self.assertEqual((p1.sqft, p1.beds, p1.baths, p1.half_baths), (1500, 3, 2, 1))
        self.assertEqual(p1.price_from, 100000)
        self.assertIsNone(p2.base_price)          # 0 means not priced
        self.assertEqual(p2.price_from, 200000)   # lowest spec price
        self.assertEqual(p2.homes_available, 2)

    def test_specs(self):
        h = self.c.homes[0]
        self.assertEqual(h.id, "A.1")
        self.assertEqual(h.slug, "1-first-st")
        self.assertEqual(h.plan, "Plan With Base")
        self.assertIsNone(h.price)
        self.assertEqual(h.move_in, date(2027, 3, 15))
        self.assertEqual(h.hero, "https://img.invalid/elev.jpg")  # inherited from plan
        self.assertEqual(h.floorplans, ["https://img.invalid/fp.jpg"])
        orphan = self.c.homes[-1]
        self.assertIsNone(orphan.plan)
        self.assertEqual(self.c.home("4-orphan-st"), orphan)

    def test_derived(self):
        self.assertEqual(self.c.price_from, 100000)
        self.assertEqual(self.c.sqft_range, (1500, 1500))
        self.assertIsNone(self.cs[0].price_from)


class FormatTest(unittest.TestCase):
    def test_missing_values_print_dash(self):
        for f in (fmt.money, fmt.num, fmt.sqft, fmt.text, fmt.move_in, fmt.baths):
            self.assertEqual(f(None), fmt.DASH)
        self.assertEqual(fmt.sqft_range(None), fmt.DASH)

    def test_values(self):
        self.assertEqual(fmt.money(349990), "$349,990")
        self.assertEqual(fmt.baths(2, 1), "2.5")
        self.assertEqual(fmt.move_in(date(2026, 1, 1), date(2026, 6, 1)), fmt.READY)
        self.assertEqual(fmt.move_in(date(2027, 3, 1), date(2026, 6, 1)), "Mar 2027")
        self.assertEqual(fmt.plan_label("Cedarwood"), "The Cedarwood Plan")
        self.assertEqual(fmt.plan_label("Cedarwood Plan"), "The Cedarwood Plan")


class PaginateTest(unittest.TestCase):
    def test_paginate(self):
        self.assertEqual(paginate([], 2, 3), [[]])
        self.assertEqual(paginate(list(range(7)), 2, 3), [[0, 1], [2, 3, 4], [5, 6]])


if __name__ == "__main__":
    unittest.main()
