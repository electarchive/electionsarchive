"""`kr.nec_links` matches each counting office to the city, county, or district of today that covers it."""

import unittest
from datetime import date
from unittest import mock

from ea.sources.kr import areas, nec_links

TODAY = ["11:중구", "26:동래구", "27:군위군", "28:미추홀구", "41:부천시", "44:당진시", "48:창원시", "50:세종특별자치시"]
PLACES = {(areas.SIGUNGU_SCHEME, code): areas.area(areas.SIGUNGU_SCHEME, code) for code in TODAY}
CASES = [
    (date(1992, 12, 18), "11", "중구", None),
    (date(1997, 12, 18), "11", "중구", "11:중구"),
    (date(1997, 12, 18), "26", "동래구갑", "26:동래구"),
    (date(2002, 12, 19), "28", "남구", "28:미추홀구"),
    (date(1997, 12, 18), "28", "남구을", "28:미추홀구"),
    (date(1997, 12, 18), "41", "부천시원미구갑", "41:부천시"),
    (date(2007, 12, 19), "44", "당진군", "44:당진시"),
    (date(2007, 12, 19), "48", "마산시", "48:창원시"),
    (date(1997, 12, 18), "48", "마산시합포구", "48:창원시"),
    (date(2007, 12, 19), "44", "연기군", "50:세종특별자치시"),
    (date(2022, 3, 9), "47", "군위군", "27:군위군"),
    (date(2007, 12, 19), "11", "없는구", None),
]


class Sigungu(unittest.TestCase):
    def test_sigungu(self) -> None:
        with mock.patch.object(nec_links, "places", lambda: PLACES):
            for day, code, town, expected in CASES:
                with self.subTest(day=day, code=code, town=town):
                    self.assertEqual(nec_links.sigungu(day, code, town), expected)

    def test_merged_on(self) -> None:
        self.assertEqual(nec_links.merged_on("48", "마산시합포구"), date(2010, 7, 1))
        self.assertEqual(nec_links.merged_on("44", "연기군"), date(2012, 7, 1))
        self.assertIsNone(nec_links.merged_on("11", "중구"))


if __name__ == "__main__":
    unittest.main()
