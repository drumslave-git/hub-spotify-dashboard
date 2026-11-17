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
        self.saved = []  # liked tracks, newest first

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
        if path == "/playlists/shared":
            return 200, {"name": "A friend's mix", "images": []}
        if path == "/me/tracks":
            start = int((query or {}).get("offset", 0))
            page = self.saved[start:start + int(query["limit"])]
            return 200, {"total": len(self.saved), "items": [
                {"track": {"uri": uri, "album": {"images": [{"url": "cover-of-" + uri}]}}} for uri in page]}
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
        before = time.time() - 1
        self.sp.expect_takeover()
        self.api.player = None            # the Hub's Spotify app was closed
        # The page that was closed for the Hub's Spotify app still counts as
        # listening for a moment: that must not start the move.
        self.sp.set_local_device("dash", listening_since=before)
        self.assertEqual(self.api.starts(), [])
        self.sp.set_local_device("dash", listening_since=time.time() + 1)  # re-cast page listens
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

    def test_takeover_tries_again_when_the_music_does_not_start(self):
        self.api.player = {
            "is_playing": True, "progress_ms": 1000, "context": None,
            "item": {"name": "Voice", "uri": "spotify:track:v", "artists": []},
            "device": {"id": "hub", "name": "Kitchen display"},
        }
        notes = []
        self.sp.note = notes.append
        self.sp.expect_takeover()
        real = self.api.__call__
        stalled = {"left": 1}

        def api(method, path, query=None, body=None):
            status, payload = real(method, path, query, body)
            if path == "/me/player/play" and stalled["left"]:
                stalled["left"] -= 1
                self.api.player = None    # accepted, then nothing plays
            return status, payload

        self.sp._api = api
        self.sp._local_id = "dash"
        self.sp._transfer_to_local()
        plays = [c for c in self.api.calls if c[1] == "/me/player/play"]
        self.assertEqual(len(plays), 2)
        self.assertIn("takeover: music continues on the dashboard", notes)

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
            self.played("c", "spotify:playlist:no-access"),
            self.played("d", "spotify:playlist:mix"),
            self.played("e"),                             # from Liked Songs: no context
            self.played("f", "spotify:playlist:shared"),  # someone else's, but readable
        ]
        lib = self.sp.library()
        self.assertEqual([e["uri"] for e in lib["playlists"]], ["spotify:playlist:mix"])
        self.assertEqual([(e["uri"], e["name"]) for e in lib["recent"]],
                         [("spotify:playlist:mix", "Mix"), ("spotify:album:a1", "Album"),
                          ("spotify:playlist:shared", "A friend's mix")])
        # A playlist that could not be read is not asked about again.
        self.sp._library = None
        self.sp.library()
        self.assertEqual(sum(1 for c in self.api.calls if c[1] == "/playlists/no-access"), 1)
        self.assertEqual(lib["playlists"][0]["art"], "big")

    def test_library_offers_liked_songs(self):
        self.assertIsNone(self.sp.library()["liked"])  # nothing liked: no entry
        self.sp._library = None
        self.api.saved = ["spotify:track:%d" % n for n in range(120)]
        liked = self.sp.library()["liked"]
        self.assertEqual((liked["uri"], liked["name"], liked["kind"]), ("liked", "Liked Songs", "120 songs you liked"))
        self.assertEqual(liked["art"], "cover-of-spotify:track:0")

    def test_playing_liked_songs_queues_them_newest_first(self):
        self.now_playing()
        self.api.saved = ["spotify:track:%d" % n for n in range(120)]
        self.assertTrue(self.sp.command("play", "liked")[0])
        method, path, query, body = self.last_call()
        self.assertEqual((method, path, query), ("PUT", "/me/player/play", {"device_id": "phone"}))
        self.assertEqual(body, {"uris": self.api.saved})

    def test_liked_songs_are_capped(self):
        self.now_playing()
        self.api.saved = ["spotify:track:%d" % n for n in range(mod.LIKED_MAX + 130)]
        self.sp.command("play", "liked")
        self.assertEqual(len(self.last_call()[3]["uris"]), mod.LIKED_MAX)

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

    def test_what_plays_is_asked_again_only_when_it_can_have_changed(self):
        self.now_playing(item={"name": "song", "uri": "spotify:track:song", "duration_ms": 200000})
        asked = lambda: sum(1 for c in self.api.calls if c[:2] == ("GET", "/me/player"))
        self.sp.state()
        self.sp._state_at = time.time() - 10
        self.sp.state()
        self.assertEqual(asked(), 1)  # mid-song: kept
        self.sp._state_at = time.time() - mod.PLAYING_FOR - 1
        self.sp.state()
        self.assertEqual(asked(), 2)  # now and then, for a pause or skip elsewhere
        self.api.player["progress_ms"] = 198000
        self.sp._state_at = 0
        self.sp.state()
        self.sp._state_at = time.time() - 3.5
        self.sp.state()
        self.assertEqual(asked(), 4)  # just after the song should have ended
        self.api.player = None
        self.sp._state_at = 0
        self.sp.state()
        self.sp._state_at = time.time() - mod.IDLE_FOR + 5
        self.sp.state()
        self.assertEqual(asked(), 5)  # nothing plays: rarely

    def dashboard_plays(self, **more):
        """The dashboard's own player says what it plays."""
        self.sp.set_local_device("dash")
        self.sp.set_local_playback("dash", {
            "playing": True, "position_ms": 61000, "duration_ms": 200000, "at": 1234,
            "shuffle": True, "repeat": "context", "context_uri": "spotify:playlist:mix",
            "device_name": "Dashboard",
            "track": {"uri": "spotify:track:here", "name": "Here", "artists": [{"name": "A"}, {"name": "B"}],
                      "album": {"uri": "spotify:album:al", "name": "Al",
                                "images": [{"url": "small", "height": 64}, {"url": "mid", "height": 300},
                                           {"url": "big", "height": 640}]}},
            "next": {"title": "After", "artist": "C"},
            **more,
        })

    def asked_player(self):
        return sum(1 for c in self.api.calls if c[:2] == ("GET", "/me/player"))

    def test_music_on_the_dashboard_is_known_without_asking_spotify(self):
        self.dashboard_plays()
        self.api.liked = True
        for _ in range(3):
            self.sp._state_at = 0
            state = self.sp.state()
        self.assertEqual(state["title"], "Here")
        self.assertEqual(state["artist"], "A, B")
        self.assertEqual(state["art"], "mid")
        self.assertEqual((state["device"], state["device_id"], state["on_dashboard"]), ("Dashboard", "dash", True))
        self.assertEqual((state["progress_ms"], state["at"], state["playing"]), (61000, 1234, True))
        self.assertEqual((state["shuffle"], state["repeat"]), (True, "context"))
        self.assertEqual(state["next"], {"title": "After", "artist": "C"})
        self.assertTrue(state["liked"])
        self.assertEqual(state["source"], "dashboard player")
        # Only "is it liked", once for the song: no polling, no queue.
        self.assertEqual([c[1] for c in self.api.calls], ["/me/library/contains"])

    def test_music_leaving_the_dashboard_asks_spotify_where_it_went(self):
        self.dashboard_plays()
        self.sp.state()
        self.now_playing()
        self.sp.set_local_playback("dash", None)
        state = self.sp.state()
        self.assertEqual(self.asked_player(), 1)
        self.assertEqual((state["device"], state["source"]), ("Phone", "Spotify"))

    def test_a_dashboard_player_gone_quiet_is_not_trusted(self):
        self.dashboard_plays()
        self.sp._local_playback["received"] -= mod.LOCAL_FRESH + 1
        self.now_playing()
        self.assertEqual(self.sp.state()["device"], "Phone")
        self.assertEqual(self.asked_player(), 1)

    def test_presses_on_music_on_the_dashboard_do_not_ask_what_plays(self):
        self.dashboard_plays()
        self.assertTrue(self.sp.command("toggle")[0])
        self.assertEqual(self.last_call()[:2], ("PUT", "/me/player/pause"))
        self.assertEqual(self.asked_player(), 0)

    def test_not_connected(self):
        self.sp._refresh = None
        self.assertEqual(self.sp.command("toggle"), (False, "Spotify is not connected."))
        self.assertFalse(self.sp.state()["connected"])


class RefusalTests(unittest.TestCase):
    """What happens when Spotify answers 429 (the real _api, a fake network)."""

    def setUp(self):
        self._http = mod._http
        self.sent = []
        self.refuse = {}  # path -> (Retry-After, reason)
        mod._http = self.fake_http
        self.notes = []
        self.sp = mod.Spotify("cid", "http://127.0.0.1/cb", Path(tempfile.mkdtemp()) / "s.json",
                              note=self.notes.append)
        self.sp._refresh, self.sp._access, self.sp._expires = "refresh", "token", time.time() + 3600

    def tearDown(self):
        mod._http = self._http

    def fake_http(self, method, url, headers=None, data=None):
        path = url[len(mod.API):].split("?")[0]
        self.sent.append((method, path))
        if path in self.refuse:
            wait, reason = self.refuse[path]
            error = {"status": 429, "message": "Too many requests", **({"reason": reason} if reason else {})}
            return 429, {"error": error}, {"Retry-After": str(wait)}
        if path == "/me/player":
            return 204, None, {}
        if path == "/me/player/devices":
            return 200, {"devices": [{"id": "dash", "name": "Dashboard"}]}, {}
        return 200, {"items": []}, {}

    def test_a_long_refusal_stops_only_that_endpoint(self):
        self.refuse["/me/library/contains"] = (3 * 3600, "")
        self.assertEqual(self.sp._api("GET", "/me/library/contains")[0], 429)
        self.assertEqual(self.sp._api("GET", "/me/library/contains")[0], 429)
        self.assertEqual(self.sent.count(("GET", "/me/library/contains")), 1)  # not asked again
        self.assertEqual(self.sp._api("PUT", "/me/player/pause")[0], 200)     # play and pause still work
        self.assertIn("GET /me/library/contains", self.sp.limits())
        self.assertIn("wait 10800s", self.sp.errors[-1])
        self.assertTrue(any("refused for 10800s" in n for n in self.notes))
        self.sp._api("GET", "/me/library/contains")
        self.assertIn("refuses this until", self.sp._reason(429, None))

    def test_a_used_up_quota_stops_only_that_endpoint_even_when_short(self):
        self.refuse["/me/player/queue"] = (30, "QUOTA_EXCEEDED")
        self.sp._api("GET", "/me/player/queue")
        self.assertEqual(self.sp._api("GET", "/me/player/recently-played")[0], 200)
        self.assertEqual(list(self.sp.limits()), ["GET /me/player/queue"])

    def test_a_short_refusal_makes_every_request_wait(self):
        self.refuse["/me/player/queue"] = (5, "")
        self.sp._api("GET", "/me/player/queue")
        self.assertEqual(self.sp._api("GET", "/me/player/recently-played")[0], 429)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("in a moment", self.sp._reason(429, None))

    def test_the_screen_says_when_spotify_will_not_say_what_plays(self):
        self.refuse["/me/player"] = (7200, "")
        self.assertIn("Spotify stopped answering until", self.sp.state()["error"])

    def test_play_says_why_it_cannot_find_what_played_last(self):
        sleep, mod.time.sleep = mod.time.sleep, lambda _s: None
        self.addCleanup(setattr, mod.time, "sleep", sleep)
        self.sp.set_local_device("dash")
        self.refuse["/me/player/recently-played"] = (7200, "QUOTA_EXCEEDED")
        ok, message = self.sp.command("toggle")
        self.assertFalse(ok)
        self.assertIn("won't say what played last until", message)
        self.assertNotIn("Nothing to resume", message)

    def test_the_wait_is_shown_with_its_day(self):
        self.assertNotIn(" ", self.sp._when(time.time()))
        self.assertTrue(self.sp._when(time.time() + 86400).startswith("tomorrow "))


if __name__ == "__main__":
    unittest.main()
