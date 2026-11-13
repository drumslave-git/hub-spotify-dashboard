"""Nest Hub dashboard: serves the page and keeps it on the Hub's screen.

Run:  python server.py [--no-keeper]
Settings come from environment variables (see docker-compose.yml), falling
back to config.json for local development.
"""
from __future__ import annotations

import html
import json
import logging
import mimetypes
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pychromecast
from pychromecast.controllers.dashcast import DashCastController

from player import SILENCE, LocalPlayer, wav_header
from spotify import Spotify

ROOT = Path(__file__).parent
WEB = ROOT / "web"


def read_version() -> str:
    """The release this is (the VERSION file, shipped in the image)."""
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


VERSION = read_version()


def load_config() -> dict:
    """Environment variables win; config.json (if present) supplies the rest."""
    path = ROOT / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    env = os.environ

    cfg["hub_host"] = env.get("HUB_HOST") or cfg.get("hub_host")
    if not cfg["hub_host"]:
        raise SystemExit("HUB_HOST is not set (the Nest Hub's IP address)")
    cfg["port"] = int(env.get("PORT") or cfg.get("port") or 8765)
    cfg["timezone"] = env.get("TZ") or cfg.get("timezone") or "UTC"
    if env.get("HOUR12"):
        cfg["hour12"] = env["HOUR12"].lower() in ("1", "true", "yes")

    weather = dict(cfg.get("weather") or {})
    weather["name"] = env.get("WEATHER_NAME") or weather.get("name") or ""
    weather["latitude"] = float(env.get("WEATHER_LAT") or weather.get("latitude") or 0)
    weather["longitude"] = float(env.get("WEATHER_LON") or weather.get("longitude") or 0)
    cfg["weather"] = weather

    # The address the Hub loads the page from. If not given, use this machine's
    # address on the route to the Hub (correct with host networking; in a
    # bridged container it would be the container's address, so set PUBLIC_URL).
    cfg["public_url"] = env.get("PUBLIC_URL") or cfg.get("public_url")
    if not cfg["public_url"]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((cfg["hub_host"], 8009))
            cfg["public_url"] = f"http://{probe.getsockname()[0]}:{cfg['port']}/"

    sp = dict(cfg.get("spotify") or {})
    sp["client_id"] = env.get("SPOTIFY_CLIENT_ID") or sp.get("client_id") or ""
    # Spotify only accepts https redirect addresses, or http on 127.0.0.1, so
    # the default follows the dashboard's own address when that is https.
    if cfg["public_url"].startswith("https://"):
        default_redirect = cfg["public_url"].rstrip("/") + "/api/spotify/callback"
    else:
        default_redirect = f"http://127.0.0.1:{cfg['port']}/api/spotify/callback"
    sp["redirect_uri"] = env.get("SPOTIFY_REDIRECT_URI") or sp.get("redirect_uri") or default_redirect
    # Spotify device to start music on when nothing is playing and the page's
    # own player is not available.
    sp["device"] = env.get("SPOTIFY_DEVICE") or sp.get("device") or ""
    # Night mode: between these hours ("HH:MM") the page shows only a dim
    # clock while nothing plays. "off" (or equal hours) switches it off.
    night_from = (env.get("NIGHT_FROM") or cfg.get("night_from") or "23:00").strip()
    night_to = (env.get("NIGHT_TO") or cfg.get("night_to") or "07:00").strip()
    hhmm = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
    if not (hhmm.fullmatch(night_from) and hhmm.fullmatch(night_to)):
        night_from = night_to = ""   # "off", or anything that is not a time
    cfg["night"] = {"from": night_from, "to": night_to}
    # Name the dashboard's own player shows up as in Spotify.
    sp["player_name"] = env.get("SPOTIFY_PLAYER_NAME") or sp.get("player_name") or "Dashboard"
    cfg["spotify"] = sp

    # Your own registered Cast app (its receiver URL is the dashboard address).
    # Empty = use the public DashCast app.
    cfg["cast_app_id"] = (env.get("CAST_APP_ID") or cfg.get("cast_app_id") or "").strip()
    takeover = env.get("VOICE_TAKEOVER") or str(cfg.get("voice_takeover", "true"))
    cfg["voice_takeover"] = takeover.lower() in ("1", "true", "yes")
    cfg["data_dir"] = env.get("DATA_DIR") or cfg.get("data_dir") or str(ROOT / "data")
    return cfg


CONFIG = load_config()

APP_BACKDROP = "E8C28D3C"  # the Hub's ambient / idle screen
APP_DASHCAST = "84912283"

log = logging.getLogger("hubdash")


# ---------------------------------------------------------------- weather

class Weather:
    """Open-Meteo forecast, cached so the Hub never talks to the internet itself."""

    TTL = 600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._fetched = 0.0

    def get(self) -> dict | None:
        with self._lock:
            if time.time() - self._fetched > self.TTL:
                try:
                    self._data = self._fetch()
                except Exception as err:  # keep serving the last good data
                    log.warning("weather fetch failed: %s", err)
                self._fetched = time.time()
            return self._data

    def _fetch(self) -> dict:
        w = CONFIG["weather"]
        query = urllib.parse.urlencode(
            {
                "latitude": w["latitude"],
                "longitude": w["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,"
                "is_day,wind_speed_10m,relative_humidity_2m",
                "hourly": "temperature_2m,weather_code,precipitation_probability,precipitation,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,sunrise,sunset",
                "timezone": CONFIG["timezone"],
                "forecast_days": 4,
                "wind_speed_unit": "ms",
            }
        )
        url = f"https://api.open-meteo.com/v1/forecast?{query}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = json.load(resp)

        cur = raw["current"]
        hourly = raw["hourly"]
        start = next(
            (i for i, t in enumerate(hourly["time"]) if t > cur["time"]), 0
        )
        hours = [
            {
                "time": hourly["time"][i],
                "temp": hourly["temperature_2m"][i],
                "code": hourly["weather_code"][i],
                "rain": hourly["precipitation_probability"][i],
                "is_day": hourly["is_day"][i],
            }
            for i in range(start, min(start + 12, len(hourly["time"])), 3)
        ]
        # Chance and amount of rain or snow for each of the next 12 hours.
        rain = [
            {
                "time": hourly["time"][i],
                "chance": hourly["precipitation_probability"][i] or 0,
                "mm": hourly["precipitation"][i] or 0,
                "code": hourly["weather_code"][i],
            }
            for i in range(start, min(start + 12, len(hourly["time"])))
        ]
        daily = raw["daily"]
        days = [
            {
                "date": daily["time"][i],
                "code": daily["weather_code"][i],
                "max": daily["temperature_2m_max"][i],
                "min": daily["temperature_2m_min"][i],
                "rain": daily["precipitation_probability_max"][i],
                # local times, "YYYY-MM-DDTHH:MM": the page's sky follows them
                "sunrise": daily["sunrise"][i],
                "sunset": daily["sunset"][i],
            }
            for i in range(len(daily["time"]))
        ]
        return {
            "place": w["name"],
            "temp": cur["temperature_2m"],
            "feels": cur["apparent_temperature"],
            "code": cur["weather_code"],
            "is_day": cur["is_day"],
            "wind": cur["wind_speed_10m"],
            "humidity": cur["relative_humidity_2m"],
            "hours": hours,
            "rain": rain,
            "days": days,
            "fetched": int(time.time()),
        }


# ----------------------------------------------------------------- keeper

class Keeper(threading.Thread):
    """Casts the dashboard whenever the Hub is idle, and re-casts if it dies.

    Other Cast apps (YouTube, ...) are left alone; the dashboard comes back
    once the Hub returns to its ambient screen. The exception is Spotify: when
    the dashboard page can play Spotify itself, a Spotify session started on
    the Hub (e.g. by voice) is closed and its music moved into the dashboard.
    """

    POLL = 5            # seconds between checks
    IDLE_SETTLE = 8     # Hub must be idle this long before we cast
    MIN_RECAST = 45     # never cast more often than this
    BEAT_TIMEOUT = 75   # page is considered dead without a heartbeat this long
    TAKEOVER_DELAY = 6  # let a voice-started Spotify session begin before moving it

    def __init__(self) -> None:
        super().__init__(daemon=True, name="keeper")
        self.state = "starting"
        self.started = time.time()
        self.app_id: str | None = None
        self.app_name: str | None = None
        self.casts = 0
        self.last_cast = 0.0
        self.last_beat = 0.0
        self.beat: dict = {}
        self.events: list[str] = []

    def note(self, text: str) -> None:
        log.info(text)
        self.events.append(time.strftime("%H:%M:%S ") + text)
        del self.events[:-60]

    def beat_summary(self) -> str:
        """The page's last heartbeat in a few words, for the stale note."""
        if not self.last_beat:
            return "none"
        beat = self.beat
        parts = [f"{time.time() - self.last_beat:.0f}s ago", f"page up {beat.get('up')}s"]
        for key in ("sent", "timers", "mem"):
            if beat.get(key) is not None:
                parts.append(f"{key} {json.dumps(beat[key], separators=(',', ':'))}")
        return ", ".join(parts)

    def run(self) -> None:
        while True:
            try:
                self._session()
            except Exception as err:
                self.state = "error"
                self.note(f"keeper error: {err!r}; reconnecting in 15s")
                time.sleep(15)

    def _session(self) -> None:
        host = CONFIG["hub_host"]
        cast = pychromecast.get_chromecast_from_host((host, 8009, None, None, None))
        cast.wait(timeout=20)
        dash = DashCastController()
        cast.register_handler(dash)
        self.note(f"connected to hub at {host}")
        ours = {APP_DASHCAST, CONFIG["cast_app_id"]} - {""}
        idle_since: float | None = None
        spotify_since: float | None = None
        hurry = False   # cast right away (after closing an app ourselves)
        try:
            while True:
                time.sleep(self.POLL)
                status = cast.status
                if status is None:
                    continue
                now = time.time()
                if (self.app_id, self.app_name) != (status.app_id, status.display_name):
                    self.note(f"hub app: {status.display_name} ({status.app_id})")
                self.app_id, self.app_name = status.app_id, status.display_name
                if status.display_name != "Spotify":
                    spotify_since = None

                if status.app_id in (None, APP_BACKDROP):
                    self.state = "idle"
                    idle_since = idle_since or now
                    if hurry or (
                        now - idle_since >= self.IDLE_SETTLE
                        and now - self.last_cast >= self.MIN_RECAST
                    ):
                        hurry = False
                        self._cast(cast, dash, "hub is idle")
                elif status.app_id in ours:
                    idle_since = None
                    # After a service restart the page is usually still up on the
                    # Hub; give it time to report in before re-casting.
                    since_alive = now - max(self.last_beat, self.last_cast, self.started)
                    if since_alive > self.BEAT_TIMEOUT:
                        self.state = "stale"
                        # Our page has replaced DashCast's own receiver, so it
                        # can't be told to load a URL; close it and start over.
                        self.note(
                            "page stopped reporting; closing it to re-cast "
                            f"(last beat: {self.beat_summary()})"
                        )
                        cast.quit_app()  # the idle branch re-casts a few seconds later
                    else:
                        self.state = "showing"
                else:
                    idle_since = None
                    self.state = "other-app"
                    if (
                        status.display_name == "Spotify"
                        and CONFIG["voice_takeover"]
                        and spotify.local_recent
                    ):
                        spotify_since = spotify_since or now
                        if now - spotify_since >= self.TAKEOVER_DELAY:
                            self.note("Spotify started on the Hub; moving it into the dashboard")
                            spotify.expect_takeover()
                            cast.quit_app()
                            spotify_since, hurry = None, True
        finally:
            cast.disconnect(timeout=5)

    def _cast(self, cast, dash: DashCastController, why: str) -> None:
        self.casts += 1
        self.last_cast = time.time()
        self.note(f"casting dashboard ({why})")
        if CONFIG["cast_app_id"]:
            # Your own Cast app: Google loads its registered receiver address.
            cast.start_app(CONFIG["cast_app_id"])
        else:
            dash.load_url(CONFIG["public_url"], force=True)


# ------------------------------------------------------------------- http

weather = Weather()
keeper = Keeper()
spotify = Spotify(
    CONFIG["spotify"]["client_id"],
    CONFIG["spotify"]["redirect_uri"],
    Path(CONFIG["data_dir"]) / "spotify.json",
    CONFIG["spotify"]["device"],
    note=keeper.note,
)


# The dashboard's own Spotify device; its audio is streamed to the page.
local = LocalPlayer(CONFIG["spotify"]["player_name"], CONFIG["port"], note=keeper.note)


def link_player() -> None:
    """Tell the Spotify side which device is the dashboard, while the player is
    signed in and a page is actually listening to its audio."""
    while True:
        time.sleep(3)
        if local.ready and local.hub.listeners:
            spotify.set_local_device(local.device_id, listening_since=local.hub.joined)


def test_tone() -> None:
    """--test-tone: a quiet beep pattern instead of the player, to try the
    page's audio path without Spotify."""
    import math

    frames = b"".join(
        struct.pack("<hh", v, v)
        for v in (
            int(2500 * math.sin(2 * math.pi * 440 * i / 44100)) if i < 44100 // 4 else 0
            for i in range(44100)
        )
    )
    position = 0

    def read(n: int) -> bytes:
        nonlocal position
        chunk = frames[position:position + n] or frames[:n]
        position = (position + len(chunk)) % len(frames)
        return chunk

    local.state = "test"
    local.hub.pump(read)


def page_version() -> str:
    return str(int(max(p.stat().st_mtime for p in WEB.rglob("*") if p.is_file())))


class Handler(BaseHTTPRequestHandler):
    server_version = "hubdash"

    def log_message(self, fmt: str, *args) -> None:  # quiet access log
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, message: str, status: int = 200) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><title>Kitchen display</title>"
            "<body style='font:18px system-ui;padding:40px;background:#0b0d10;color:#f3f0ea'>"
            f"{html.escape(message)}</body>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            self._json(
                {
                    "now": int(time.time() * 1000),
                    "timezone": CONFIG["timezone"],
                    "hour12": CONFIG.get("hour12", False),
                    "night": CONFIG["night"],
                    "version": page_version(),
                    "weather": weather.get(),
                }
            )
        elif path == "/api/health":
            self._json({"ok": True})
        elif path == "/api/player":
            self._json({**spotify.state(), "local": local.info()})
        elif path == "/api/library":
            self._json(spotify.library() if spotify.connected else {"recent": [], "playlists": []})
        elif path == "/api/devices":
            self._json({"devices": spotify.devices() if spotify.connected else []})
        elif path in ("/api/audio", "/api/audio.wav"):
            self._audio(wav=path.endswith(".wav"))
        elif path == "/api/spotify/token":
            # Only for the player page in Chrome on this machine: a token is
            # the key to the account, so nobody else on the network gets one.
            if not self._from_here():
                self._json({"error": "not available"}, 403)
                return
            token = spotify.access_token()
            self._json({"token": token}, 200 if token else 503)
        elif path == "/api/spotify/login":
            if not spotify.configured:
                self._html("Spotify is not set up: SPOTIFY_CLIENT_ID is missing.", 400)
                return
            self.send_response(302)
            self.send_header("Location", spotify.login_url())
            self.end_headers()
        elif path == "/api/spotify/callback":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "error" in query:
                self._html(f"Spotify sign-in was cancelled ({query['error'][0]}).", 400)
                return
            error = spotify.finish_login(
                query.get("code", [""])[0], query.get("state", [""])[0]
            )
            if error:
                self._html(error, 400)
            elif spotify.store_error:
                keeper.note(f"spotify: signed in, but not saved ({spotify.store_error})")
                self._html(
                    "Spotify is connected, but the sign-in could not be saved "
                    f"({spotify.store_error}). It works until the service restarts; "
                    "make the data folder writable and sign in again to keep it.",
                    200,
                )
            else:
                keeper.note("spotify: signed in")
                self._html("Spotify is connected. You can close this tab.")
        elif path == "/api/debug":
            self._json(
                {
                    "version": VERSION,
                    "keeper": {
                        "state": keeper.state,
                        "app": keeper.app_name,
                        "app_id": keeper.app_id,
                        "casts": keeper.casts,
                        "last_cast_age": round(time.time() - keeper.last_cast, 1)
                        if keeper.last_cast
                        else None,
                        "last_beat_age": round(time.time() - keeper.last_beat, 1)
                        if keeper.last_beat
                        else None,
                    },
                    "beat": keeper.beat,
                    "player": spotify.state(),
                    "spotify_errors": list(spotify.errors),
                    "local": local.debug(),
                    "events": keeper.events,
                }
            )
        else:
            self._static(path)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if path == "/api/beat":
            try:
                beat = json.loads(raw)
            except ValueError:
                beat = {}
            beat["from"] = self.client_address[0]
            # Only heartbeats from a Cast device count as "the dashboard is
            # alive" (not a preview in a desktop browser). Matched on the user
            # agent because container networking can hide the Hub's address.
            if "CrKey" in str(beat.get("ua", "")):
                if not keeper.last_beat:
                    keeper.note(f"first heartbeat from hub: {json.dumps(beat)}")
                keeper.last_beat = time.time()
                keeper.beat = beat
            if beat.get("event"):
                keeper.note(f"page event from {beat['from']}: {beat['event']}")
            self._json({"ok": True})
        elif path == "/api/engine":
            # Status from the player page in Chrome on this machine.
            if not self._from_here():
                self._json({"error": "not available"}, 403)
                return
            try:
                local.report(json.loads(raw))
            except ValueError:
                pass
            self._json({"ok": True})
        elif path == "/api/player":
            action, ok, message = "?", False, "Bad request."
            try:
                req = json.loads(raw)
                action = str(req.get("action"))
                ok, message = spotify.command(action, req.get("value"))
            except Exception as err:
                log.warning("player command failed: %r", err)
                message = "Something went wrong."
            keeper.note(
                f"player {action} from {self.client_address[0]}: "
                f"{'ok' if ok else 'failed: ' + message}"
            )
            self._json({"ok": ok, "message": message})
        else:
            self._json({"error": "not found"}, 404)

    def _from_here(self) -> bool:
        """The request came from this machine itself, not through the network
        or a reverse proxy."""
        return self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("X-Forwarded-For")

    def _audio(self, wav: bool) -> None:
        """The player's sound as an endless stream of 16-bit stereo 44.1 kHz
        samples (with a WAV header on request)."""
        if local.state == "off":
            self._json({"error": "this server has no built-in player"}, 404)
            return
        feed = local.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav" if wav else "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")  # ask proxies not to hold it back
            self.end_headers()
            if wav:
                self.wfile.write(wav_header())
            while True:
                try:
                    chunk = feed.get(timeout=10)
                except queue.Empty:
                    # Nothing playing: a blip of silence keeps the connection
                    # open and shows when the listener has gone.
                    chunk = SILENCE
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # the listener went away
        finally:
            local.hub.unsubscribe(feed)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        # A browser that reloads or leaves mid-response is not an error.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    if "--no-keeper" not in sys.argv:
        keeper.start()
    else:
        keeper.state = "disabled"
    if "--test-tone" in sys.argv:
        threading.Thread(target=test_tone, name="tone", daemon=True).start()
    elif local.enabled:
        local.start()
        threading.Thread(target=link_player, name="link", daemon=True).start()
    else:
        log.info("no built-in Spotify player here (needs Chrome and PulseAudio)")
    server = Server(("0.0.0.0", CONFIG["port"]), Handler)
    log.info("hubdash %s serving on port %s, casting %s", VERSION, CONFIG["port"], CONFIG["public_url"])
    server.serve_forever()


if __name__ == "__main__":
    main()
