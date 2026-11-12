"""Spotify control logic against a fake Web API (no network)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spotify as mod  # noqa: E402


class FakeApi:
    """Minimal stand-in for the Spotify Web API."""

    def __init__(self):
        self.calls = []
        self.devices = [
            {"id": "hub", "name": "Kitchen display", "is_active": False},
            {"id": "phone", "name": "Phone", "is_active": False},
        ]
        self.player = None  # None = nothing active
        self.recent = None  # None = one track from a playlist

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if path == "/me/player/devices":
            return 200, {"devices": self.devices}
        if path == "/me/player" and method == "GET":
            return (200, self.player) if self.player else (204, None)
        if path == "/me/player/recently-played":
            track = {
                "name": "Last",
                "uri": "spotify:track:last",
                "artists": [{"name": "A"}],
                "album": {"name": "Al", "images": [{"url": "big"}, {"url": "mid"}]},
            }
            if self.recent is not None:
                return 200, {"items": self.recent}
            return 200, {"items": [{"track": track, "context": {"uri": "spotify:playlist:p1"}}]}
        if path == "/me/player/play":
            self.player = {
                "is_playing": True,
                "item": {"name": "Last", "uri": "spotify:track:last"},
                "device": {"id": (query or {}).get("device_id", "x"), "name": "Dashboard"},
            }
            return 204, None
        if path in ("/me/player", "/me/player/pause", "/me/player/next",
                    "/me/player/previous", "/me/player/volume"):
            return 204, None
        return 404, None

    def starts(self):
        """Calls that would start or move playback."""
        return [c for c in self.calls
                if c[0] == "PUT" and c[1] in ("/me/player", "/me/player/play")]


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        self._sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None  # don't wait in retries
        self.sp = mod.Spotify("cid", "http://127.0.0.1/cb", Path(tempfile.mkdtemp()) / "s.json")
        self.sp._refresh = "refresh"
        self.api = FakeApi()
        self.sp._api = self.api

    def tearDown(self):
        mod.time.sleep = self._sleep

    def test_play_refuses_without_in_page_player(self):
        """Idle and no in-page player: never start the Hub's own app or a random device."""
        ok, message = self.sp.command("toggle")
        self.assertFalse(ok)
        self.assertIn("by itself", message)
        self.assertEqual(self.api.starts(), [])

    def test_play_resumes_on_dashboard(self):
        self.api.devices.append({"id": "dash", "name": "Dashboard", "is_active": False})
        self.sp.set_local_device("dash")
        ok, _ = self.sp.command("toggle")
        self.assertTrue(ok)
        starts = self.api.starts()
        self.assertTrue(starts)
        self.assertTrue(all("dash" in str(call) for call in starts))
        self.assertTrue(
            any(c[3] and c[3].get("context_uri") == "spotify:playlist:p1" for c in starts)
        )

    def test_toggle_pauses_where_music_is_playing(self):
        self.api.player = {
            "is_playing": True,
            "progress_ms": 5,
            "item": {"name": "T", "uri": "u", "duration_ms": 1000, "artists": []},
            "device": {"id": "phone", "name": "Phone", "volume_percent": 40, "supports_volume": True},
        }
        ok, _ = self.sp.command("toggle")
        self.assertTrue(ok)
        self.assertIn(("PUT", "/me/player/pause", None, None), self.api.calls)
        state = self.sp.state()
        self.assertTrue(state["active"])
        self.assertEqual(state["device"], "Phone")
        self.assertFalse(state["on_dashboard"])

    def test_takeover_continues_track_and_position(self):
        self.api.player = {
            "is_playing": True,
            "progress_ms": 42000,
            "context": {"uri": "spotify:album:a1"},
            "item": {"name": "Voice", "uri": "spotify:track:v", "artists": []},
            "device": {"id": "hub", "name": "Kitchen display"},
        }
        self.sp.expect_takeover()
        self.api.player = None            # the Hub's Spotify app was closed
        self.sp.set_local_device("dash")  # page reports in -> transfer starts
        for _ in range(50):
            if any(c[1] == "/me/player/play" for c in self.api.calls):
                break
            self._sleep(0.02)
        plays = [c for c in self.api.calls if c[1] == "/me/player/play"]
        self.assertTrue(plays)
        self.assertEqual(plays[0][2], {"device_id": "dash"})
        self.assertEqual(plays[0][3]["position_ms"], 42000)
        self.assertEqual(plays[0][3]["context_uri"], "spotify:album:a1")
        self.assertEqual(plays[0][3]["offset"], {"uri": "spotify:track:v"})
        self.assertEqual(self.sp._takeover_until, 0)  # one-shot

    @staticmethod
    def played(name, context=None, album="spotify:album:" + "a1"):
        track = {"name": name, "uri": "spotify:track:" + name, "artists": [], "album": {"uri": album, "images": []}}
        return {"track": track, "context": {"uri": context} if context else None}

    def play_calls(self):
        return [c for c in self.api.calls if c[0] == "PUT" and c[1] == "/me/player/play"]

    def test_resume_prefers_the_latest_playlist_over_a_lone_track(self):
        self.sp.set_local_device("dash")
        self.api.devices.append({"id": "dash", "name": "Dashboard"})
        self.api.recent = [self.played("lone"), self.played("song", "spotify:playlist:mix")]
        ok, _ = self.sp._resume(fresh=True)
        self.assertTrue(ok)
        self.assertEqual(self.play_calls()[-1][3],
                         {"context_uri": "spotify:playlist:mix", "offset": {"uri": "spotify:track:song"}})

    def test_lone_track_continues_with_its_album(self):
        self.sp.set_local_device("dash")
        self.api.devices.append({"id": "dash", "name": "Dashboard"})
        self.api.recent = [self.played("lone")]
        self.assertTrue(self.sp._resume(fresh=True)[0])
        self.assertEqual(self.play_calls()[-1][3],
                         {"context_uri": "spotify:album:a1", "offset": {"uri": "spotify:track:lone"}})

    def test_play_after_a_lone_track_ran_out_starts_something_that_continues(self):
        self.sp.set_local_device("dash")
        self.api.devices.append({"id": "dash", "name": "Dashboard"})
        self.api.recent = [self.played("lone"), self.played("song", "spotify:playlist:mix")]
        self.api.player = {"is_playing": False, "progress_ms": 0, "context": None,
                           "item": {"name": "lone", "uri": "spotify:track:lone"},
                           "device": {"id": "dash", "name": "Dashboard"}}
        self.assertTrue(self.sp.command("toggle")[0])
        self.assertEqual(self.play_calls()[-1][3]["context_uri"], "spotify:playlist:mix")

    def test_play_resumes_a_paused_playlist_in_place(self):
        self.api.player = {"is_playing": False, "progress_ms": 42000, "context": {"uri": "spotify:playlist:mix"},
                           "item": {"name": "song", "uri": "spotify:track:song"},
                           "device": {"id": "phone", "name": "Phone"}}
        self.assertTrue(self.sp.command("toggle")[0])
        self.assertEqual(self.play_calls()[-1][2:], (None, None))  # plain resume, nothing chosen

    def test_in_page_player_expires_when_page_goes_quiet(self):
        self.sp.set_local_device("dash")
        self.sp._local_at = time.time() - 120
        self.assertFalse(self.sp.local_ready)
        self.assertTrue(self.sp.local_recent)

    def test_sign_in_survives_an_unwritable_data_folder(self):
        blocker = Path(tempfile.mkdtemp()) / "not-a-folder"
        blocker.write_text("x")
        sp = mod.Spotify("cid", "http://127.0.0.1/cb", blocker / "s.json")
        sp._refresh = "refresh"
        sp._save()  # must not raise
        self.assertIn("s.json", sp.store_error)
        self.assertTrue(sp.connected)

    def test_saved_sign_in_is_read_back(self):
        self.sp._save()
        self.assertEqual(self.sp.store_error, "")
        again = mod.Spotify("cid", "http://127.0.0.1/cb", self.sp._store)
        self.assertTrue(again.connected)

    def test_not_connected(self):
        self.sp._refresh = None
        self.assertEqual(self.sp.command("toggle"), (False, "Spotify is not connected."))
        self.assertFalse(self.sp.state()["connected"])


if __name__ == "__main__":
    unittest.main()
