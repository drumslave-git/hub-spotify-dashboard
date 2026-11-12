"""The dashboard's own Spotify player.

Spotify's official web player (the Web Playback SDK, in web/engine.html) runs
in Chrome on the server and shows up in Spotify as a device. Chrome's sound
goes to a virtual sound card (PulseAudio); this module records that and hands
it to whoever is listening on /api/audio, which is the dashboard page on the
Hub. So the music plays through the page, and the Hub never has to switch to
Spotify's own app.

Why not play in the Hub's browser directly: Spotify's player refuses to start
there. Why not an open-source Spotify client: Spotify refuses those the track
keys on newer accounts. Chrome with the official player is what works.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

log = logging.getLogger("player")

RATE = 44100
CHANNELS = 2
FRAME = 2 * CHANNELS            # bytes per sample frame (16-bit stereo)
BYTES_PER_SECOND = RATE * FRAME
CHUNK = BYTES_PER_SECOND // 10  # 100 ms
SILENCE = bytes(CHUNK)


def wav_header() -> bytes:
    """Header for an endless 16-bit stereo WAV stream."""
    return (
        b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE, BYTES_PER_SECOND, FRAME, 16)
        + b"data" + struct.pack("<I", 0xFFFFFFFF)
    )


class AudioHub:
    """Fans the player's audio out to listeners, no faster than real time."""

    LEAD = 0.3          # seconds a listener may be sent ahead of real time
    BACKLOG = 50        # chunks kept for a slow listener before the oldest is dropped
    QUIET_CHUNKS = 5    # this much unbroken silence, and the rest is not sent

    def __init__(self) -> None:
        self._listeners: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self.bytes_in = 0
        self.last_audio = 0.0

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(self.BACKLOG)
        with self._lock:
            self._listeners.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._listeners.discard(q)

    def publish(self, chunk: bytes) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(chunk)
            except queue.Full:
                # A listener that fell behind skips ahead rather than lag forever.
                try:
                    q.get_nowait()
                    q.put_nowait(chunk)
                except (queue.Empty, queue.Full):
                    pass

    def pump(self, read, sleep=time.sleep, now=time.monotonic) -> None:
        """Read audio with `read(n)` until it returns nothing, publishing it paced.

        Only whole sample frames are published, so a listener that joins late
        or loses a chunk never ends up with the channels swapped. A sound card
        keeps producing silence while nothing plays; after a moment of it the
        silence is dropped instead of being streamed around the clock.
        """
        pending = b""
        clock = None
        quiet = 0
        while True:
            data = read(CHUNK)
            if not data:
                return
            pending += data
            usable = len(pending) - len(pending) % FRAME
            if not usable:
                continue
            chunk, pending = pending[:usable], pending[usable:]
            if chunk.count(0) == len(chunk):
                quiet += 1
                if quiet > self.QUIET_CHUNKS:
                    continue
            else:
                quiet = 0
                self.last_audio = time.time()
            t = now()
            if clock is None or clock < t - 0.5:
                clock = t  # first audio after a silence: start the clock afresh
            clock += len(chunk) / BYTES_PER_SECOND
            self.bytes_in += len(chunk)
            self.publish(chunk)
            ahead = clock - now()
            if ahead > self.LEAD:
                sleep(ahead - self.LEAD)


class LocalPlayer(threading.Thread):
    """Keeps Chrome, its virtual sound card and the recorder running, and
    tracks what the player page inside Chrome reports."""

    daemon = True
    STALE = 60   # seconds without a report from the player page before a restart

    def __init__(self, name: str, port: int, note=None) -> None:
        super().__init__(name="player")
        self.page = f"http://127.0.0.1:{port}/engine.html?name={urllib.parse.quote(name)}"
        self.hub = AudioHub()
        self.chrome = os.environ.get("CHROME_BIN") or shutil.which("google-chrome")
        self._note = note or (lambda _text: None)
        # off | starting | waiting | ready | error
        self.state = "starting" if self.enabled else "off"
        self.message = ""
        self.device_id: str | None = None
        self.last_report = 0.0
        self.restarts = 0

    @property
    def enabled(self) -> bool:
        return bool(self.chrome and shutil.which("pulseaudio") and shutil.which("parec"))

    @property
    def ready(self) -> bool:
        return self.state == "ready" and bool(self.device_id)

    def info(self) -> dict:
        out = {"state": self.state, "listeners": self.hub.listeners}
        if self.message:
            out["message"] = self.message
        return out

    def debug(self) -> dict:
        return {
            **self.info(),
            "device_id": self.device_id,
            "restarts": self.restarts,
            "report_age": round(time.time() - self.last_report, 1) if self.last_report else None,
            "audio_mb": round(self.hub.bytes_in / 1e6, 1),
            "last_audio_age": round(time.time() - self.hub.last_audio, 1) if self.hub.last_audio else None,
        }

    # ---------------------------------------------- reports from the player page

    def report(self, data: dict) -> None:
        """What web/engine.html says about Spotify's player inside Chrome."""
        event = str(data.get("event") or "")
        before = (self.state, self.message)
        self.last_report = time.time()
        if event in ("ready", "alive") and data.get("device"):
            self.state, self.message, self.device_id = "ready", "", str(data["device"])
        elif event == "waiting":      # nobody has signed in to Spotify yet
            self.state, self.message, self.device_id = "waiting", "", None
        elif event == "error":
            text = str(data.get("message") or "")
            self.state, self.device_id = "error", None
            self.message = (
                "Spotify Premium is needed to play here" if text.startswith("account_error")
                else "Spotify's player could not start"
            )
            log.warning("player page: %s", text)
        elif event == "not_ready":
            self.state, self.message, self.device_id = "starting", "", None
        elif event == "playback_error":
            self._note(f"player: playback error: {data.get('message')}")
        if (self.state, self.message) != before:
            self._note(f"player: {self.state}" + (f" ({data.get('message')})" if self.state == "error" else ""))

    # --------------------------------------------------------------- processes

    def run(self) -> None:
        if not self.enabled:
            return
        home = Path(tempfile.gettempdir()) / "hubdash-player"
        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_RUNTIME_DIR": str(home / "run"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
        }
        wait = 5
        while True:
            started = time.time()
            procs: list[subprocess.Popen] = []
            try:
                shutil.rmtree(home, ignore_errors=True)
                (home / "run").mkdir(parents=True)
                os.chmod(home / "run", 0o700)
                quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}

                # A sound card that exists only in software; "dash.monitor" is
                # a recording of whatever is played into it.
                procs.append(subprocess.Popen(
                    ["pulseaudio", "-n", "--daemonize=no", "--exit-idle-time=-1",
                     "--load=module-native-protocol-unix",
                     f"--load=module-null-sink sink_name=dash rate={RATE} channels={CHANNELS} format=s16le"],
                    **quiet))
                for _ in range(50):
                    if subprocess.run(["pactl", "info"], **quiet).returncode == 0:
                        break
                    time.sleep(0.2)
                else:
                    raise OSError("the sound server did not start")

                recorder = subprocess.Popen(
                    ["parec", "--device=dash.monitor", "--format=s16le", f"--rate={RATE}",
                     f"--channels={CHANNELS}", "--latency-msec=40"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
                procs.append(recorder)
                fd = recorder.stdout.fileno()
                threading.Thread(
                    target=self.hub.pump, args=(lambda n: os.read(fd, n),), name="audio", daemon=True
                ).start()

                procs.append(subprocess.Popen(
                    [self.chrome, "--headless=new", "--no-sandbox", "--no-first-run", "--disable-gpu",
                     "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required",
                     # Keep playing at full speed although nobody looks at the window.
                     "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                     "--disable-backgrounding-occluded-windows",
                     "--disable-features=Translate,MediaRouter", "--password-store=basic",
                     f"--user-data-dir={home / 'chrome'}", "--window-size=400,300", self.page],
                    **quiet))
                self._note("player: started")
                self.last_report = 0.0

                while all(p.poll() is None for p in procs):
                    time.sleep(2)
                    age = time.time() - (self.last_report or started)
                    if age > self.STALE:
                        raise OSError(f"no word from the player page for {int(age)} s")
                raise OSError("a player process stopped")
            except OSError as err:
                self._note(f"player: restarting ({err})")
            finally:
                for p in reversed(procs):
                    p.kill()
                for p in procs:
                    p.wait()
            self.state, self.message, self.device_id = "starting", "", None
            self.restarts += 1
            wait = 5 if time.time() - started > 120 else min(wait * 2, 120)
            time.sleep(wait)
