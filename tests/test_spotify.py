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
        self.liked = False
        self.queue = []
        self.playlists = []

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if path == "/me/player/devices":
            return 200, {"devices": self.devices}
        if path == "/me/library/contains":
            return 200, [self.liked]
        if path == "/me/player/queue":
            return 200, {"queue": self.queue}
        if path == "/me/playlists":
            return 200, {"items": self.playlists}
        if path in ("/me/library", "/me/player/shuffle", "/me/player/repeat", "/me/player/seek"):
            return 200, None
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
                    "/me/player/previous"):
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

    def now_playing(self, **more):
        self.api.player = {"is_playing": True, "progress_ms": 5000, "context": {"uri": "spotify:playlist:mix"},
                           "item": {"name": "song", "uri": "spotify:track:song"},
                           "device": {"id": "phone", "name": "Phone"}, **more}

    def last_call(self):
        return self.api.calls[-1]

    def test_like_saves_and_unlike_removes_the_playing_track(self):
        self.now_playing()
        self.assertTrue(self.sp.command("like")[0])
        self.assertEqual(self.last_call()[:3], ("PUT", "/me/library", {"uris": "spotify:track:song"}))
        self.assertTrue(self.sp.state()["liked"])  # shown at once, without asking Spotify again
        self.assertTrue(self.sp.command("like")[0])
        self.assertEqual(self.last_call()[:3], ("DELETE", "/me/library", {"uris": "spotify:track:song"}))

    def test_liked_and_up_next_are_asked_once_per_track(self):
        self.now_playing()
        self.api.liked = True
        self.api.queue = [{"name": "after", "artists": [{"name": "B"}]}]
        first = self.sp.state()
        self.assertTrue(first["liked"])
        self.assertEqual(first["next"], {"title": "after", "artist": "B"})
        self.sp._state_at = 0
        self.sp.state()
        asked = [c[1] for c in self.api.calls if c[1] in ("/me/library/contains", "/me/player/queue")]
        self.assertEqual(asked, ["/me/library/contains", "/me/player/queue"])

    def test_shuffle_repeat_and_seek(self):
        self.now_playing(shuffle_state=False, repeat_state="off")
        self.sp.command("shuffle")
        self.assertEqual(self.last_call()[:3], ("PUT", "/me/player/shuffle", {"state": "true"}))
        self.sp.command("repeat")
        self.assertEqual(self.last_call()[:3], ("PUT", "/me/player/repeat", {"state": "context"}))
        self.api.player["repeat_state"] = "track"
        self.sp.command("repeat")
        self.assertEqual(self.last_call()[2], {"state": "off"})
        self.sp.command("seek", 61000.7)
        self.assertEqual(self.last_call()[:3], ("PUT", "/me/player/seek", {"position_ms": 61000}))

    def test_picking_a_device_moves_the_music_and_keeps_it_playing(self):
        self.now_playing()
        self.assertTrue(self.sp.command("device", "dash")[0])
        self.assertEqual(self.last_call()[:4], ("PUT", "/me/player", None, {"device_ids": ["dash"], "play": True}))
        self.api.player["is_playing"] = False
        self.sp.command("device", "dash")
        self.assertFalse(self.last_call()[3]["play"])  # paused music stays paused

    def test_picking_a_playlist_plays_it_where_music_is(self):
        self.now_playing()
        self.assertTrue(self.sp.command("play", "spotify:playlist:new")[0])
        self.assertEqual(self.last_call()[:4], ("PUT", "/me/player/play", {"device_id": "phone"},
                                                {"context_uri": "spotify:playlist:new"}))

    def test_picking_a_playlist_with_nothing_playing_uses_the_dashboard(self):
        self.sp.set_local_device("dash")
        self.api.devices.append({"id": "dash", "name": "Dashboard"})
        self.assertTrue(self.sp.command("play", "spotify:album:x")[0])
        self.assertEqual(self.play_calls()[-1][2], {"device_id": "dash"})

    def test_library_lists_playlists_and_what_was_played_lately(self):
        self.api.playlists = [{"uri": "spotify:playlist:mix", "name": "Mix", "images": [{"url": "big"}]}, None]
        self.api.recent = [
            self.played("a", "spotify:playlist:mix"),
            self.played("b", "spotify:album:a1"),
            self.played("c", "spotify:playlist:someone-elses"),
            self.played("d", "spotify:playlist:mix"),
        ]
        lib = self.sp.library()
        self.assertEqual([e["uri"] for e in lib["playlists"]], ["spotify:playlist:mix"])
        self.assertEqual([(e["uri"], e["kind"]) for e in lib["recent"]],
                         [("spotify:playlist:mix", "Playlist"), ("spotify:album:a1", "Album")])
        self.assertEqual(lib["playlists"][0]["art"], "big")

    def test_devices_put_the_dashboard_first(self):
        self.sp.set_local_device("dash")
        self.api.devices.append({"id": "dash", "name": "Zed display", "is_active": True})
        names = [(d["name"], d["here"], d["active"]) for d in self.sp.devices()]
        self.assertEqual(names[0], ("Zed display", True, True))
        self.assertEqual(len(names), 3)

    def test_asks_for_a_new_sign_in_when_permissions_are_missing(self):
        self.assertFalse(self.sp.needs_login)  # unknown until Spotify says what was granted
        self.sp._scope = {"streaming", "user-read-playback-state"}
        self.assertTrue(self.sp.needs_login)
        self.sp._scope = set(mod.SCOPES.split())
        self.assertFalse(self.sp.needs_login)

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
