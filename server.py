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
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pychromecast
from pychromecast.controllers.dashcast import DashCastController

from spotify import Spotify

ROOT = Path(__file__).parent
WEB = ROOT / "web"


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
    # Name the dashboard's own in-page player shows up as in Spotify.
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
                "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
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
        daily = raw["daily"]
        days = [
            {
                "date": daily["time"][i],
                "code": daily["weather_code"][i],
                "max": daily["temperature_2m_max"][i],
                "min": daily["temperature_2m_min"][i],
                "rain": daily["precipitation_probability_max"][i],
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
                        self.note("page stopped reporting; closing it to re-cast")
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
)


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
                    "version": page_version(),
                    "weather": weather.get(),
                }
            )
        elif path == "/api/health":
            self._json({"ok": True})
        elif path == "/api/player":
            self._json({**spotify.state(), "player_name": CONFIG["spotify"]["player_name"]})
        elif path == "/api/spotify/token":
            # For the page's own in-browser Spotify player.
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
            else:
                keeper.note("spotify: signed in")
                self._html("Spotify is connected. You can close this tab.")
        elif path == "/api/debug":
            self._json(
                {
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
            # A page running Spotify's in-browser player reports its device id.
            if "sdkDevice" in beat:
                spotify.set_local_device(beat.get("sdkDevice"))
            if beat.get("event"):
                keeper.note(f"page event from {beat['from']}: {beat['event']}")
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    if "--no-keeper" not in sys.argv:
        keeper.start()
    else:
        keeper.state = "disabled"
    server = ThreadingHTTPServer(("0.0.0.0", CONFIG["port"]), Handler)
    log.info("serving on port %s, casting %s", CONFIG["port"], CONFIG["public_url"])
    server.serve_forever()


if __name__ == "__main__":
    main()
