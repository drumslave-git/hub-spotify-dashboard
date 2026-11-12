"""Settings come from environment variables (what the container uses)."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {
    "HUB_HOST": "192.0.2.10",
    "PUBLIC_URL": "https://hub.example.com/",
    "PORT": "9000",
    "TZ": "Europe/London",
    "WEATHER_NAME": "London",
    "WEATHER_LAT": "51.5",
    "WEATHER_LON": "-0.12",
    "SPOTIFY_CLIENT_ID": "abc",
    "CAST_APP_ID": " ABCD1234 ",
    "VOICE_TAKEOVER": "false",
}


@unittest.skipUnless(importlib.util.find_spec("pychromecast"), "pychromecast is not installed")
class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, ENV):
            import server
        cls.server = server
        # Ignore any local config.json: only the environment should matter here.
        cls.server.ROOT = Path(tempfile.mkdtemp())

    def load(self, **overrides):
        """Load settings with ENV plus overrides; an override of None removes the variable."""
        env = {k: v for k, v in {**ENV, **overrides}.items() if v is not None}
        with mock.patch.dict(os.environ, env, clear=False):
            for key, value in overrides.items():
                if value is None:
                    os.environ.pop(key, None)
            return self.server.load_config()

    def test_environment_values(self):
        cfg = self.load()
        self.assertEqual(cfg["hub_host"], "192.0.2.10")
        self.assertEqual(cfg["public_url"], "https://hub.example.com/")
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["timezone"], "Europe/London")
        self.assertEqual(cfg["weather"], {"name": "London", "latitude": 51.5, "longitude": -0.12})
        self.assertEqual(cfg["spotify"]["client_id"], "abc")
        self.assertEqual(cfg["spotify"]["player_name"], "Dashboard")
        self.assertEqual(cfg["cast_app_id"], "ABCD1234")
        self.assertFalse(cfg["voice_takeover"])

    def test_spotify_redirect_follows_an_https_address(self):
        cfg = self.load()
        self.assertEqual(cfg["spotify"]["redirect_uri"], "https://hub.example.com/api/spotify/callback")

    def test_spotify_redirect_is_loopback_on_plain_http(self):
        cfg = self.load(PUBLIC_URL="http://192.0.2.20:9000/")
        self.assertEqual(cfg["spotify"]["redirect_uri"], "http://127.0.0.1:9000/api/spotify/callback")

    def test_spotify_redirect_can_be_set(self):
        cfg = self.load(SPOTIFY_REDIRECT_URI="https://other.example.com/cb")
        self.assertEqual(cfg["spotify"]["redirect_uri"], "https://other.example.com/cb")

    def test_empty_values_count_as_unset(self):
        """Compose passes unset variables as empty strings."""
        cfg = self.load(CAST_APP_ID="", VOICE_TAKEOVER="", SPOTIFY_CLIENT_ID="")
        self.assertEqual(cfg["cast_app_id"], "")
        self.assertTrue(cfg["voice_takeover"])
        self.assertEqual(cfg["spotify"]["client_id"], "")

    def test_missing_hub_host_stops_with_a_message(self):
        with self.assertRaises(SystemExit) as stop:
            self.load(HUB_HOST=None)
        self.assertIn("HUB_HOST", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
