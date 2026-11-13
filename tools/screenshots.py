"""Regenerate the README screenshots in docs/screenshots/.

Run:  python tools/screenshots.py

Serves the page from this process on a spare port, with made-up weather for a
neutral place and no Spotify sign-in, and has headless Chrome (or Edge)
capture it at the Hub's 1024x600 in the page's demo states (?demo=, ?at=).
The weather is fixed per shot, so only real changes to the page change the
pictures (apart from the date).
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PORT = 8799
TZ = "Europe/London"
SUNRISE, SUNSET = "07:12", "16:18"

# name -> (page query, weather now, rain chance % for each of the next 12 hours)
# Keep in sync with the README's screenshot section.
SHOTS = {
    "day": ("demo=idle&at=10:20", dict(code=2, temp=11, feels=9, wind=3),
            [0, 0, 5, 10, 20, 35, 50, 60, 45, 30, 15, 10]),
    "playing": ("demo=playing&at=15:35", dict(code=1, temp=9, feels=7, wind=2),
                [0] * 12),
    "rain": ("demo=plain&at=14:05", dict(code=63, temp=8, feels=5, wind=6),
             [90, 85, 70, 55, 40, 25, 15, 10, 5, 5, 0, 0]),
    "night": ("demo=night&at=23:40", dict(code=0, temp=4, feels=2, wind=1),
              [0] * 12),
}


def weather_at(at: str, now: dict, chances: list[int]) -> dict:
    """The server's weather data, as if fetched at `at` today."""
    today = dt.date.today()
    hh, mm = map(int, at.split(":"))
    start = dt.datetime.combine(today, dt.time(hh)) + dt.timedelta(hours=1)
    rise, set_ = (dt.datetime.combine(today, dt.time(*map(int, s.split(":")))) for s in (SUNRISE, SUNSET))

    def is_day(t: dt.datetime) -> int:
        return int(rise.time() <= t.time() < set_.time())

    def code(t: dt.datetime, chance: int) -> int:
        return 61 if chance >= 55 else 3 if chance >= 25 else 2 if is_day(t) else 1

    hours = [start + dt.timedelta(hours=i) for i in range(12)]
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M")
    days = []
    for i, (c, hi, lo, r) in enumerate([(2, 12, 6, 60), (3, 10, 5, 20), (61, 9, 4, 80), (1, 11, 3, 5)]):
        d = today + dt.timedelta(days=i)
        days.append({"date": d.isoformat(), "code": c, "max": hi, "min": lo, "rain": r,
                     "sunrise": f"{d}T{SUNRISE}", "sunset": f"{d}T{SUNSET}"})
    current = dt.datetime.combine(today, dt.time(hh, mm))
    return {
        "place": "London", "humidity": 80, **now,
        "is_day": is_day(current),
        "hours": [{"time": iso(t), "temp": now["temp"] - i // 3, "code": code(t, chances[i]),
                   "rain": chances[i], "is_day": is_day(t)} for i, t in enumerate(hours)][::3],
        "rain": [{"time": iso(t), "chance": chances[i], "mm": chances[i] / 40 if chances[i] >= 30 else 0,
                  "code": code(t, chances[i])} for i, t in enumerate(hours)],
        "days": days,
        "fetched": int(time.time()),
    }


def find_browser() -> str:
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise SystemExit("Chrome not found; set CHROME_BIN")


def main() -> None:
    browser = find_browser()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as profile:
        # server.py reads its settings when imported; these win over config.json.
        os.environ.update(
            HUB_HOST="127.0.0.1", PORT=str(PORT), PUBLIC_URL=f"http://127.0.0.1:{PORT}/",
            TZ=TZ, HOUR12="false", NIGHT_FROM="off", WEATHER_NAME="London",
            SPOTIFY_CLIENT_ID="screenshots", CAST_APP_ID="", DATA_DIR=data,
        )
        sys.path.insert(0, str(ROOT))
        import server

        server.keeper.state = "disabled"
        shown: dict = {}
        server.weather.get = lambda: shown.get("w")
        httpd = server.Server(("127.0.0.1", PORT), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            for name, (query, now, chances) in SHOTS.items():
                shown["w"] = weather_at(query.split("at=")[1], now, chances)
                path = OUT / f"{name}.png"
                subprocess.run(
                    [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                     "--no-first-run", f"--user-data-dir={profile}",
                     "--window-size=1024,600", "--force-device-scale-factor=1",
                     "--virtual-time-budget=6000", f"--screenshot={path}",
                     f"http://127.0.0.1:{PORT}/?{query}&cast=0"],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                )
                print(f"{path.relative_to(ROOT)}  ({path.stat().st_size // 1024} KB)")
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    main()
