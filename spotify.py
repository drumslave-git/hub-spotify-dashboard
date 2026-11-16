"""Spotify for the dashboard: one-time sign-in, what's playing, and controls.

This side uses only the official Web API (the dashboard's own player, which
produces the sound, is in player.py). Sign-in is the authorization-code flow with
PKCE, so only the app's client ID is needed (no client secret). The refresh
token is kept in the data directory.
"""
from __future__ import annotations

import base64
import collections
import datetime
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("hubdash.spotify")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = (
    # The first three are what Spotify's web player needs to play music.
    "streaming user-read-email user-read-private "
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing user-read-recently-played "
    # Likes, and the list of playlists to start music from.
    "user-library-read user-library-modify "
    "playlist-read-private playlist-read-collaborative"
)


# "Liked Songs" in the list of things to play, and how many of them are queued.
LIKED = "liked"
LIKED_MAX = 300

# How long, in seconds, Spotify's answer about what plays is kept. The page
# asks the server every 2 s, but every request to Spotify counts against a
# quota for the developer account, and asking every 2-5 s around the clock
# used it up (2026-11-16: refused for over 20 minutes). So Spotify is asked
# again when the song should have ended, and otherwise only now and then, to
# catch a pause or skip made somewhere else. A press on the dashboard asks
# at once.
PLAYING_FOR = 15
PAUSED_FOR = 30
IDLE_FOR = 30

# A 429 with a short Retry-After is the rate limit (a rolling 30 s window for
# the whole app), so every request waits. A long one means a used-up quota,
# which Spotify counts per group of endpoints, so only the refused endpoint
# waits: a used-up "is this song liked" must not take play and pause down
# with it.
SHORT_WAIT = 120

# While music plays on the dashboard's own player, that player says what it
# plays (see web/src/engine.ts), every 10 s and at each change, and Spotify
# is not asked. A report older than this means the player has gone quiet.
LOCAL_FRESH = 25


def _http(method: str, url: str, headers: dict | None = None, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            status, raw, hdrs = resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as err:
        status, raw, hdrs = err.code, err.read(), err.headers
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return status, body, hdrs


class Spotify:
    def __init__(self, client_id: str, redirect_uri: str, store: Path, device: str = "",
                 note=log.info) -> None:
        self.client_id = client_id
        self.note = note  # for events worth seeing on /api/debug
        self.redirect_uri = redirect_uri
        self.preferred_device = device
        self._store = store
        self._lock = threading.RLock()
        self._refresh: str | None = None
        self._access: str | None = None
        self._expires = 0.0
        self._pending: dict[str, str] = {}      # login state -> PKCE verifier
        self._backoff_until = 0.0               # every request waits (rate limit)
        self._blocked: dict[str, float] = {}    # endpoint -> refused until (quota)
        self._refused_until = 0.0               # of the latest 429, for messages
        self._state: dict | None = None
        self._state_at = 0.0
        self._state_local = False               # _state came from the dashboard's player
        self._local_playback: dict | None = None
        self._last: dict | None = None
        self._last_at = 0.0
        # The dashboard's own player (see player.py), as a Spotify device.
        self._local_id: str | None = None
        self._local_at = 0.0
        self._takeover_until = 0.0
        self._takeover_at = 0.0
        self._handoff: dict | None = None
        # What Spotify granted at sign-in (None until a token response says).
        self._scope: set[str] | None = None
        # Per-track extras: is it liked, and what comes next.
        self._extras: dict = {}
        self._library: dict | None = None
        self._named: dict[str, dict | None] = {}   # playlists looked up by URI (None: no access)
        self._library_at = 0.0
        # Recent refusals from Spotify, for /api/debug.
        self.errors: collections.deque = collections.deque(maxlen=12)
        self.error = ""
        # Set when the sign-in could not be written to disk.
        self.store_error = ""
        try:
            saved = json.loads(store.read_text(encoding="utf-8"))
            self._refresh = saved.get("refresh_token")
            if saved.get("scope"):
                self._scope = set(saved["scope"].split())
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return bool(self.client_id and self._refresh)

    # --------------------------------------------------------------- sign-in

    def login_url(self) -> str:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        with self._lock:
            self._pending = {state: verifier}
        return AUTH_URL + "?" + urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": SCOPES,
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            }
        )

    def finish_login(self, code: str, state: str) -> str | None:
        """Exchange the code for tokens. Returns an error message, or None."""
        with self._lock:
            verifier = self._pending.pop(state, None)
        if not verifier:
            return "This sign-in link has expired. Start again from /api/spotify/login."
        status, body, _ = _http(
            "POST",
            TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "code_verifier": verifier,
                }
            ).encode(),
        )
        if status != 200 or not body or "refresh_token" not in body:
            return f"Spotify refused the sign-in ({status}): {(body or {}).get('error_description', body)}"
        with self._lock:
            self._refresh = body["refresh_token"]
            self._access = body["access_token"]
            self._expires = time.time() + body.get("expires_in", 3600)
            if body.get("scope"):
                self._scope = set(body["scope"].split())
            self.error = ""
            self._state = None
            self._library = None
            self._extras = {}
            self._save()
        log.info("spotify: signed in")
        return None

    def _save(self) -> None:
        """Keep the sign-in across restarts. A failure must not undo the sign-in."""
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            saved = {"refresh_token": self._refresh, "scope": " ".join(sorted(self._scope or []))}
            self._store.write_text(json.dumps(saved), encoding="utf-8")
            self.store_error = ""
        except OSError as err:
            self.store_error = f"{self._store}: {err.strerror or err}"
            log.error("spotify: could not save the sign-in (%s)", self.store_error)

    def _token(self) -> str | None:
        with self._lock:
            if self._access and time.time() < self._expires - 60:
                return self._access
            if not self._refresh:
                return None
            status, body, _ = _http(
                "POST",
                TOKEN_URL,
                {"Content-Type": "application/x-www-form-urlencoded"},
                urllib.parse.urlencode(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh,
                        "client_id": self.client_id,
                    }
                ).encode(),
            )
            if status != 200 or not body:
                log.warning("spotify: token refresh failed (%s): %s", status, body)
                if status in (400, 401):
                    self.error = "Spotify sign-in expired. Sign in again."
                return None
            self._access = body["access_token"]
            self._expires = time.time() + body.get("expires_in", 3600)
            scope = set(body["scope"].split()) if body.get("scope") else self._scope
            changed = scope != self._scope
            self._scope = scope
            if body.get("refresh_token") and body["refresh_token"] != self._refresh:
                self._refresh = body["refresh_token"]
                changed = True
            if changed:
                self._save()
            self.error = ""
            return self._access

    def access_token(self) -> str | None:
        """A current access token (Spotify's web player on the server needs one)."""
        return self._token()

    # ------------------------------------------------------------------- api

    @staticmethod
    def _endpoint(method: str, path: str) -> str:
        """The endpoint a request goes to (one playlist stands for all)."""
        return method + " " + ("/playlists/{id}" if path.startswith("/playlists/") else path)

    def _api(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        token = self._token()
        if not token:
            return 0, None
        endpoint = self._endpoint(method, path)
        until = max(self._backoff_until, self._blocked.get(endpoint, 0.0))
        if time.time() < until:
            self._refused_until = until
            return 429, None
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": "Bearer " + token}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif method in ("PUT", "POST"):
            data = b""
        status, payload, hdrs = _http(method, url, headers, data)
        if status >= 400:
            error = ((payload or {}).get("error") or {}) if isinstance(payload, dict) else {}
            message = (error.get("message") if isinstance(error, dict) else error) or ""
            if status == 429:
                message += self._refused(endpoint, hdrs, error if isinstance(error, dict) else {})
            self.errors.append(f"{time.strftime('%H:%M:%S')} {method} {path} -> {status} {message}".strip())
        return status, payload

    def _refused(self, endpoint: str, hdrs, error: dict) -> str:
        """Wait as long as Spotify asks after a 429. Returns what to add to
        the error list."""
        try:
            wait = max(1, int(hdrs.get("Retry-After") or 5))
        except (TypeError, ValueError):
            wait = 5
        reason = error.get("reason") or ""
        until = time.time() + wait
        self._refused_until = until
        if wait > SHORT_WAIT or reason == "QUOTA_EXCEEDED":
            self._blocked[endpoint] = until
            self.note(f"spotify: {endpoint} refused for {wait}s "
                      f"({reason or 'too many requests'}), until {self._when(until)}")
        else:
            self._backoff_until = until
            log.warning("spotify: rate limited, every request waits %ss", wait)
        return f" (wait {wait}s{', ' + reason if reason else ''})"

    @staticmethod
    def _when(moment: float) -> str:
        """A time to show on screen, with the day when it is not today."""
        clock = time.strftime("%H:%M", time.localtime(moment))
        days = (datetime.date.fromtimestamp(moment) - datetime.date.today()).days
        if days == 0:
            return clock
        if days == 1:
            return "tomorrow " + clock
        return time.strftime("%d %b ", time.localtime(moment)) + clock

    def limits(self) -> dict[str, str]:
        """What Spotify refuses for now, and until when (for /api/debug)."""
        now = time.time()
        out = {endpoint: self._when(until) for endpoint, until in self._blocked.items() if until > now}
        if self._backoff_until > now:
            out["every request"] = time.strftime("%H:%M:%S", time.localtime(self._backoff_until))
        return out

    def _refusal_note(self) -> str:
        """Said on screen while Spotify will not say what plays."""
        until = self._blocked.get("GET /me/player", 0.0)
        if until - time.time() <= 0:
            return ""
        return f"Spotify stopped answering until {self._when(until)}: the dashboard asked too often."

    @property
    def needs_login(self) -> bool:
        """Signed in before the dashboard asked for everything it uses now."""
        return self.connected and self._scope is not None and not set(SCOPES.split()) <= self._scope

    @staticmethod
    def _describe(item: dict | None) -> dict:
        item = item or {}
        images = [i for i in (item.get("album") or item.get("show") or item).get("images") or [] if i.get("url")]
        # About 300 px is plenty. The Web API lists covers largest first;
        # the dashboard's player may list them in another order.
        if images and all(i.get("height") for i in images):
            art = min(images, key=lambda i: abs(i["height"] - 300))["url"]
        else:
            art = images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else None)
        artists = ", ".join(a["name"] for a in item.get("artists") or [])
        return {
            "title": item.get("name") or "",
            "artist": artists or (item.get("show") or {}).get("name", ""),
            "album": (item.get("album") or {}).get("name", ""),
            "art": art,
            "duration_ms": item.get("duration_ms") or 0,
            "uri": item.get("uri"),
            "album_uri": (item.get("album") or {}).get("uri"),
        }

    def _fetch_state(self) -> dict:
        status, p = self._api("GET", "/me/player", {"additional_types": "track,episode"})
        if status == 200 and p and p.get("item"):
            device = p.get("device") or {}
            return {
                "active": True,
                "playing": bool(p.get("is_playing")),
                **self._describe(p["item"]),
                "device": device.get("name") or "",
                "device_id": device.get("id"),
                "progress_ms": p.get("progress_ms") or 0,
                "context_uri": (p.get("context") or {}).get("uri"),
                "shuffle": bool(p.get("shuffle_state")),
                "repeat": p.get("repeat_state") or "off",
                "at": int(time.time() * 1000),
                **self._track_extras(p["item"].get("uri")),
            }
        if status in (200, 202, 204):
            return {"active": False, "playing": False, "last": self._last_played()}
        # Network trouble or rate limit: keep showing what we had.
        if self._state is not None:
            return self._state
        return {"active": False, "playing": False, "last": None}

    def _last_played(self) -> dict | None:
        if time.time() - self._last_at < 600:
            return self._last
        status, p = self._api("GET", "/me/player/recently-played", {"limit": 20})
        if status == 200 and p and p.get("items"):
            # The latest thing played as part of a playlist or album, so the
            # music carries on afterwards; a lone track only if there is no such.
            items = p["items"]
            entry = next((i for i in items if (i.get("context") or {}).get("uri")), items[0])
            self._last = {
                **self._describe(entry.get("track")),
                "context_uri": (entry.get("context") or {}).get("uri"),
            }
        self._last_at = time.time()
        return self._last

    @staticmethod
    def _play_body(track: dict) -> dict:
        """What to ask Spotify to play for a remembered track: its playlist or
        album starting at that track, so something follows when it ends."""
        context = track.get("context_uri") or track.get("album_uri")
        if context:
            return {"context_uri": context, "offset": {"uri": track["uri"]}}
        return {"uris": [track["uri"]]}

    def _fresh_for(self) -> float:
        """Seconds the last answer about what plays is kept (see PLAYING_FOR)."""
        known = self._state or {}
        if not known.get("active"):
            return IDLE_FOR
        if not known.get("playing"):
            return PAUSED_FOR
        if not known.get("duration_ms"):
            return PLAYING_FOR
        # Ask again just after the song should have ended, to show the next.
        left = (known["duration_ms"] - (known.get("progress_ms") or 0)) / 1000
        return max(2.0, min(PLAYING_FOR, left + 1))

    def set_local_playback(self, device_id: str | None, playback: dict | None) -> None:
        """What the dashboard's own player says it plays (None: nothing, or
        the music is on another device)."""
        with self._lock:
            self._local_playback = (
                {"device_id": device_id, "playback": playback, "received": time.time()}
                if device_id and isinstance(playback, dict) and isinstance(playback.get("track"), dict)
                else None
            )

    def _local_state(self) -> dict | None:
        """The state as the dashboard's own player last told it, without a
        request to Spotify; None when it plays nothing or has gone quiet."""
        report = self._local_playback
        if not report or time.time() - report["received"] > LOCAL_FRESH:
            return None
        playback = report["playback"]
        track = playback["track"]
        extras = self._track_extras(track.get("uri"), ask_queue=False)
        return {
            "active": True,
            "playing": bool(playback.get("playing")),
            **self._describe(track),
            "device": playback.get("device_name") or "Dashboard",
            "device_id": report["device_id"],
            "progress_ms": int(playback.get("position_ms") or 0),
            "context_uri": playback.get("context_uri"),
            "shuffle": bool(playback.get("shuffle")),
            "repeat": playback.get("repeat") or "off",
            "at": int(playback.get("at") or report["received"] * 1000),
            "liked": extras["liked"],
            "next": playback.get("next"),
        }

    def _use_local(self) -> bool:
        """Take what plays from the dashboard's own player when the music is
        there. True when it was."""
        local = self._local_state()
        if local is not None:
            self._state, self._state_at, self._state_local = local, time.time(), True
            return True
        if self._state_local:
            # The music has left the dashboard: ask Spotify where it went.
            self._state_local, self._state_at = False, 0.0
        return False

    def state(self) -> dict:
        base = {
            "configured": self.configured,
            "connected": self.connected,
            "error": self.error,
            "local_ready": self.local_ready,
            "needs_login": self.needs_login,
        }
        if not self.connected:
            return base
        with self._lock:
            if not self._use_local() and (
                self._state is None or time.time() - self._state_at > self._fresh_for()
            ):
                self._state = self._fetch_state()
                self._state_at = time.time()
            device_id = self._state.get("device_id")
            return {
                **base,
                **self._state,
                "error": self.error or self._refusal_note(),
                "on_dashboard": bool(device_id) and device_id == self._local_id,
                "source": "dashboard player" if self._state_local else "Spotify",
            }

    # ------------------------------------------------------ likes and up next

    def _track_extras(self, uri: str | None, ask_queue: bool = True) -> dict:
        """Whether the playing track is liked and what comes after it. Asked
        once per track (each request counts against Spotify's quota); a
        press on the dashboard that changes either asks again. The
        dashboard's own player knows what comes next by itself, so for it
        the queue is not asked (`ask_queue`)."""
        if not uri:
            return {"liked": None, "next": None}
        cached = self._extras
        if cached.get("uri") != uri:
            liked = None
            status, p = self._api("GET", "/me/library/contains", {"uris": uri})
            if status == 200 and isinstance(p, list) and p:
                liked = bool(p[0])
            self._extras = cached = {"uri": uri, "liked": liked}
        if ask_queue and "next" not in cached:
            upcoming = None
            status, p = self._api("GET", "/me/player/queue")
            if status == 200 and p and p.get("queue"):
                following = self._describe(p["queue"][0])
                upcoming = {"title": following["title"], "artist": following["artist"]}
            cached["next"] = upcoming
        return {"liked": cached.get("liked"), "next": cached.get("next")}

    # ------------------------------------------------ what there is to play

    def library(self) -> dict:
        """The user's playlists and the albums and playlists played lately, to
        start music from the dashboard."""
        with self._lock:
            if self._library is not None and time.time() - self._library_at < 300:
                return self._library
            def art(images):
                images = images or []
                return (images[1] if len(images) > 1 else images[0])["url"] if images else None

            playlists = []
            status, p = self._api("GET", "/me/playlists", {"limit": 50})
            for item in ((p or {}).get("items") or []) if status == 200 else []:
                if item and item.get("uri"):
                    playlists.append({"uri": item["uri"], "name": item.get("name") or "Playlist",
                                      "art": art(item.get("images")), "kind": "Playlist"})
            by_uri = {entry["uri"]: entry for entry in playlists}

            recent, seen, lookups = [], set(), 0
            status, p = self._api("GET", "/me/player/recently-played", {"limit": 50})
            for item in ((p or {}).get("items") or []) if status == 200 else []:
                context = (item.get("context") or {}).get("uri") or ""
                album = (item.get("track") or {}).get("album") or {}
                if context in seen:
                    continue
                if context in by_uri:
                    entry = by_uri[context]
                elif context and context == album.get("uri"):
                    # Only an album that was played as an album: a track played
                    # from Liked Songs or a search has no context to return to.
                    entry = {"uri": context, "name": album.get("name") or "Album",
                             "art": art(album.get("images")), "kind": "Album"}
                elif context.startswith("spotify:playlist:"):
                    # Someone else's playlist: ask for its name, a few at a time.
                    if context not in self._named and lookups < 5:
                        lookups += 1
                        status, info = self._api("GET", "/playlists/" + context.rsplit(":", 1)[1],
                                                 {"fields": "name,images"})
                        self._named[context] = (
                            {"uri": context, "name": info["name"], "art": art(info.get("images")), "kind": "Playlist"}
                            if status == 200 and info and info.get("name") else None
                        )
                    entry = self._named.get(context)
                    if entry is None:
                        continue
                else:
                    continue
                seen.add(context)
                recent.append(entry)
            # Liked Songs is not something Spotify can be told to play as a
            # whole, so it is offered as its own entry and played as a list of
            # the most recently liked tracks (see _liked_uris).
            liked = None
            status, p = self._api("GET", "/me/tracks", {"limit": 1})
            if status == 200 and p and p.get("total"):
                first = ((p.get("items") or [{}])[0].get("track") or {}).get("album") or {}
                liked = {"uri": LIKED, "name": "Liked Songs", "art": art(first.get("images")),
                         "kind": f"{p['total']} songs you liked"}
            self._library = {"liked": liked, "recent": recent[:8], "playlists": playlists}
            self._library_at = time.time()
            return self._library

    def _liked_uris(self) -> list[str]:
        """The most recently liked tracks, newest first, up to LIKED_MAX."""
        uris: list[str] = []
        while len(uris) < LIKED_MAX:
            status, p = self._api("GET", "/me/tracks", {"limit": 50, "offset": len(uris)})
            items = (p or {}).get("items") or [] if status == 200 else []
            uris += [i["track"]["uri"] for i in items if (i.get("track") or {}).get("uri")]
            if len(items) < 50:
                break
        return uris[:LIKED_MAX]

    def devices(self) -> list[dict]:
        """Where Spotify can play right now, the dashboard's own player first."""
        status, p = self._api("GET", "/me/player/devices")
        found = [
            {"id": d["id"], "name": d.get("name") or "Device", "type": (d.get("type") or "").lower(),
             "active": bool(d.get("is_active")), "here": d["id"] == self._local_id}
            for d in ((p or {}).get("devices") or []) if status == 200 and d.get("id") and not d.get("is_restricted")
        ]
        return sorted(found, key=lambda d: (not d["here"], d["name"].lower()))

    # ------------------------------------------------ the dashboard's player

    @property
    def local_ready(self) -> bool:
        """True while the dashboard's own player is signed in and a page is
        listening to its sound."""
        return bool(self._local_id) and time.time() - self._local_at < 60

    def set_local_device(self, device_id: str | None, listening_since: float | None = None) -> None:
        """Called every few seconds with the player's Spotify device id while
        it is ready and being listened to; `listening_since` is when the
        newest listener joined (None: unknown, taken as now)."""
        now = time.time()
        with self._lock:
            self._local_id = device_id or None
            self._local_at = now
            # A takeover waits for a page that started listening after it
            # began: the page that was closed for the Hub's Spotify app still
            # counts as listening for a few seconds, and moving the music
            # while that app is still closing gets it stopped again.
            fresh = (now if listening_since is None else listening_since) > self._takeover_at
            pending = bool(device_id) and now < self._takeover_until and fresh
            if pending:
                self._takeover_until = 0.0
        if pending:
            threading.Thread(target=self._transfer_to_local, daemon=True).start()

    @property
    def local_recent(self) -> bool:
        """The dashboard could play by itself within the last few minutes (the
        page stops listening while another app holds the Hub's screen)."""
        return bool(self._local_id) and time.time() - self._local_at < 300

    def expect_takeover(self) -> None:
        """Music was started elsewhere on the Hub (e.g. by voice). Remember
        what is playing, so it can continue on the dashboard's player as soon
        as the page is listening again."""
        self._handoff = None
        self._takeover_at = time.time()
        status, p = self._api("GET", "/me/player", {"additional_types": "track,episode"})
        if status == 200 and p and p.get("item"):
            self._handoff = {
                "uri": p["item"].get("uri"),
                "context_uri": (p.get("context") or {}).get("uri"),
                "position_ms": p.get("progress_ms") or 0,
            }
            seconds = self._handoff["position_ms"] // 1000
            self.note(f"takeover: noted {p['item'].get('name') or 'the track'} at {seconds // 60}:{seconds % 60:02d}")
        else:
            self.note(f"takeover: could not see what plays ({status}); will resume the last music")
        self._takeover_until = time.time() + 90

    def _transfer_to_local(self) -> None:
        handoff, self._handoff = self._handoff, None
        for attempt in range(5):
            device_id = self._local_id
            if not device_id:
                self.note("takeover: the dashboard's player went away; not moving the music")
                return
            if handoff and handoff.get("uri") and attempt < 2:
                # Continue the exact track and position that was playing.
                body = {"position_ms": handoff["position_ms"]}
                if handoff.get("context_uri"):
                    body.update(context_uri=handoff["context_uri"], offset={"uri": handoff["uri"]})
                else:
                    body["uris"] = [handoff["uri"]]
                status, _ = self._api("PUT", "/me/player/play", {"device_id": device_id}, body)
            else:
                status, _ = self._api(
                    "PUT", "/me/player", body={"device_ids": [device_id], "play": True}
                )
            if status in (200, 202, 204):
                # Spotify accepts a move before it happens; make sure it did.
                time.sleep(2.5)
                status, p = self._api("GET", "/me/player")
                device = ((p or {}).get("device") or {}).get("id") if status == 200 else None
                self._state_at = 0
                if device == device_id and (p or {}).get("is_playing"):
                    self.note("takeover: music continues on the dashboard")
                    return
                self.note(f"takeover: accepted but not playing on the dashboard (try {attempt + 1}), again")
            else:
                self.note(f"takeover: move to the dashboard refused ({status}), again")
            time.sleep(2)
        self.note("takeover: gave up moving the music to the dashboard")
        self.error = "Could not move the music to the dashboard."

    # -------------------------------------------------------------- controls

    def _pick_device(self) -> dict | None:
        status, p = self._api("GET", "/me/player/devices")
        devices = [d for d in (p or {}).get("devices") or [] if not d.get("is_restricted")]
        if status != 200 or not devices:
            return None
        if self.local_ready:
            for d in devices:
                if d.get("id") == self._local_id:
                    return d
        # Without the dashboard's own player, only start music on a device that was
        # chosen on purpose. Never fall back to the Hub's own Spotify app or a
        # random device: that would switch the Hub's screen or play elsewhere.
        wanted = self.preferred_device.strip().lower()
        for d in devices:
            if wanted and d.get("name", "").strip().lower() == wanted:
                return d
        return None

    def _resume(self, fresh: bool = False) -> tuple[bool, str]:
        """Start playback when nothing is playing: continue where Spotify left
        off, or failing that (or when `fresh`), start the most recent track's
        playlist or album."""
        device = self._pick_device()
        if device is None:
            return False, "The dashboard can't play music by itself yet."
        if not fresh:
            status, _ = self._api("PUT", "/me/player", body={"device_ids": [device["id"]], "play": True})
            if status in (200, 202, 204):
                time.sleep(1.5)
                _, p = self._api("GET", "/me/player")
                if p and p.get("is_playing"):
                    return True, ""
        self._last_at = 0  # look again: what was played last may have changed
        last = self._last_played()
        if not last or not last.get("uri"):
            return False, "Nothing to resume yet. Play something on Spotify once."
        body = self._play_body(last)
        status, payload = self._api("PUT", "/me/player/play", {"device_id": device["id"]}, body)
        if status not in (200, 202, 204) and "context_uri" in body:
            status, payload = self._api(
                "PUT", "/me/player/play", {"device_id": device["id"]}, {"uris": [last["uri"]]}
            )
        if status in (200, 202, 204):
            return True, ""
        return False, self._reason(status, payload)

    def _reason(self, status: int, payload) -> str:
        message = ((payload or {}).get("error") or {}).get("message") if isinstance(payload, dict) else ""
        if status == 403:
            return message or "Spotify refused (Premium is required for playback control)."
        if status == 429:
            if self._refused_until - time.time() > SHORT_WAIT:
                return f"Spotify refuses this until {self._when(self._refused_until)}: the dashboard asked too often."
            return "Spotify is rate limiting; try again in a moment."
        return message or f"Spotify error {status}"

    def _play_context(self, current: dict, uri: str) -> tuple[bool, str]:
        """Start a playlist or album: where music is playing now, otherwise on
        the dashboard itself."""
        if current.get("active") and current.get("device_id"):
            device_id = current["device_id"]
        else:
            device = self._pick_device()
            if device is None:
                return False, "The dashboard can't play music by itself yet."
            device_id = device["id"]
        if uri == LIKED:
            uris = self._liked_uris()
            if not uris:
                return False, "Could not read your liked songs."
            body = {"uris": uris}
        else:
            body = {"context_uri": uri}
        status, payload = self._api("PUT", "/me/player/play", {"device_id": device_id}, body)
        return (True, "") if status in (200, 202, 204) else (False, self._reason(status, payload))

    def command(self, action: str, value=None) -> tuple[bool, str]:
        if not self.connected:
            return False, "Spotify is not connected."
        with self._lock:
            current = self._state if self._use_local() else self._fetch_state()
            simple = None  # (method, path, query, body) for the one-call actions
            if action == "like":
                if not current.get("uri"):
                    return False, "Nothing is playing."
                liked = not current.get("liked")
                simple = ("PUT" if liked else "DELETE", "/me/library", {"uris": current["uri"]}, None)
            elif action == "shuffle":
                simple = ("PUT", "/me/player/shuffle", {"state": "false" if current.get("shuffle") else "true"}, None)
            elif action == "repeat":
                following = {"off": "context", "context": "track", "track": "off"}
                simple = ("PUT", "/me/player/repeat", {"state": following.get(current.get("repeat"), "context")}, None)
            elif action == "seek":
                simple = ("PUT", "/me/player/seek", {"position_ms": max(0, int(value or 0))}, None)
            elif action == "device":
                # Keep playing if it was; with nothing playing, picking a
                # device means "play there".
                keep_going = bool(current.get("playing")) or not current.get("active")
                simple = ("PUT", "/me/player", None, {"device_ids": [str(value)], "play": keep_going})
            if simple:
                status, payload = self._api(*simple)
                ok = status in (200, 202, 204)
                if ok and action == "like":
                    known = self._extras if self._extras.get("uri") == current["uri"] else {}
                    self._extras = {**known, "uri": current["uri"], "liked": simple[0] == "PUT"}
                elif ok:
                    self._extras = {}  # the queue may have changed
                self._state_at = 0
                return (True, "") if ok else (False, self._reason(status, payload))
            if action == "play":
                result = self._play_context(current, str(value or ""))
                self._extras = {}
                self._state_at = 0
                return result
            if action == "toggle":
                if current.get("active") and current.get("playing"):
                    status, payload = self._api("PUT", "/me/player/pause")
                    fresh = False
                elif current.get("active"):
                    # A single track that has run out (no playlist or album
                    # behind it): start something that carries on instead of
                    # replaying that one track.
                    fresh = not current.get("context_uri") and not current.get("progress_ms")
                    if fresh:
                        status, payload = None, None
                    else:
                        status, payload = self._api("PUT", "/me/player/play")
                        if status == 404:
                            status, payload = None, None
                else:
                    fresh, status, payload = False, None, None
                result = self._resume(fresh) if status is None else (
                    (True, "") if status in (200, 202, 204) else (False, self._reason(status, payload))
                )
            elif action in ("next", "prev"):
                path = "/me/player/next" if action == "next" else "/me/player/previous"
                status, payload = self._api("POST", path)
                result = (True, "") if status in (200, 202, 204) else (False, self._reason(status, payload))
            else:
                result = (False, "Unknown action.")
            self._state_at = 0  # refetch on the next poll
            return result
