"""Moonrise and moonset, checked against published times for London."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import moon

LONDON = (51.5, -0.12)


def first_pass(day: str) -> dict:
    start = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc)
    return moon.passes(*LONDON, start, 2, 0)[0]


def minute(iso: str) -> int:
    return int(iso[11:13]) * 60 + int(iso[14:16])


class MoonTests(unittest.TestCase):
    def test_full_moon_rises_near_sunset(self):
        # Full moon, 25 January 2024: moonrise about 16:00 UTC.
        p = first_pass("2024-01-25")
        self.assertEqual(p["rise"][:10], "2024-01-25")
        self.assertLess(abs(minute(p["rise"]) - 16 * 60), 10)
        self.assertEqual(p["set"][:10], "2024-01-26")

    def test_new_moon_rises_near_sunrise(self):
        # New moon, 8 April 2024: moonrise about 05:15 UTC, moonset about 18:45.
        p = first_pass("2024-04-08")
        self.assertLess(abs(minute(p["rise"]) - (5 * 60 + 15)), 10)
        self.assertLess(abs(minute(p["set"]) - (18 * 60 + 45)), 10)

    def test_local_times_use_the_offset(self):
        start = dt.datetime(2024, 1, 25, tzinfo=dt.timezone.utc)
        utc = moon.passes(*LONDON, start, 2, 0)[0]
        hour_ahead = moon.passes(*LONDON, start, 2, 3600)[0]
        self.assertEqual(minute(hour_ahead["rise"]), minute(utc["rise"]) + 60)

    def test_every_pass_rises_before_it_sets(self):
        start = dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc)
        passes = moon.passes(*LONDON, start, 5, 0)
        self.assertGreaterEqual(len(passes), 3)
        for p in passes:
            self.assertLess(p["rise"], p["set"])


if __name__ == "__main__":
    unittest.main()
