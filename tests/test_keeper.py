"""The keeper's note when the page stops reporting."""
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@unittest.skipUnless(importlib.util.find_spec("pychromecast"), "pychromecast is not installed")
class BeatSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {"HUB_HOST": "192.0.2.10", "PUBLIC_URL": "https://hub.example.com/"}):
            import server
        cls.server = server

    def test_version_comes_from_the_version_file(self):
        expected = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(self.server.VERSION, expected)

    def test_no_beat_yet(self):
        self.assertEqual(self.server.Keeper().beat_summary(), "none")

    def test_summary_carries_the_page_side_report(self):
        keeper = self.server.Keeper()
        keeper.last_beat = time.time() - 80
        keeper.beat = {
            "up": 1405,
            "sent": {"ok": 93, "failed": 2, "error": "Failed to fetch"},
            "timers": {"maxGap": 0, "gaps": 0},
            "mem": "12/1024 MB",
        }
        text = keeper.beat_summary()
        self.assertIn("80s ago", text)
        self.assertIn("page up 1405s", text)
        self.assertIn('"failed":2', text)
        self.assertIn('timers {"maxGap":0,"gaps":0}', text)
        self.assertIn('mem "12/1024 MB"', text)

    def test_older_page_without_the_new_fields(self):
        keeper = self.server.Keeper()
        keeper.last_beat = time.time()
        keeper.beat = {"up": 30}
        self.assertEqual(keeper.beat_summary(), "0s ago, page up 30s")

    def test_covered_page_is_recast_after_the_grace(self):
        keeper = self.server.Keeper()
        keeper.page_beat({"visible": "hidden"})
        now = time.time()
        self.assertFalse(keeper.covered_too_long(now + 5))
        keeper.last_beat = now + keeper.HIDDEN_GRACE   # still beating while covered
        self.assertTrue(keeper.covered_too_long(now + keeper.HIDDEN_GRACE + 1))

    def test_visible_beat_clears_the_cover(self):
        keeper = self.server.Keeper()
        keeper.page_beat({"visible": "hidden"})
        keeper.page_beat({"visible": "visible"})
        self.assertIsNone(keeper.hidden_since)
        self.assertFalse(keeper.covered_too_long(time.time() + 600))

    def test_hidden_page_that_stopped_beating_is_left_to_the_stale_check(self):
        keeper = self.server.Keeper()
        keeper.page_beat({"visible": "hidden"})
        self.assertFalse(keeper.covered_too_long(time.time() + keeper.BEAT_TIMEOUT + 1))


if __name__ == "__main__":
    unittest.main()
