"""The dashboard's own Spotify player.

go-librespot (an open-source Spotify Connect client) runs as a child process
and shows up in Spotify as a device. Its decoded audio comes out of a named
pipe; this module reads that at real-time speed and hands it to whoever is
listening on /api/audio, which is the dashboard page on the Hub. So the music
plays through the page, and the Hub never has to switch to Spotify's own app.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
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
    """Fans the player's audio out to listeners, no faster than real time.

    The player writes as fast as its pipe is read, and judges the playback
    position by what it has written. Reading at playing speed therefore keeps
    its idea of "now" honest.
    """

    LEAD = 0.3     # seconds a listener may be sent ahead of real time
    BACKLOG = 50   # chunks kept for a slow listener before the oldest is dropped

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
        or loses a chunk never ends up with the channels swapped.
        """
        pending = b""
        clock = None
        while True:
            data = read(CHUNK)
            if not data:
                return
            pending += data
            usable = len(pending) - len(pending) % FRAME
            if not usable:
                continue
            chunk, pending = pending[:usable], pending[usable:]
            t = now()
            if clock is None or clock < t - 0.5:
                clock = t  # first audio after a silence: start the clock afresh
            clock += len(chunk) / BYTES_PER_SECOND
            self.bytes_in += len(chunk)
            self.last_audio = time.time()
            self.publish(chunk)
            ahead = clock - now()
            if ahead > self.LEAD:
                sleep(ahead - self.LEAD)


class LocalPlayer(threading.Thread):
    """Runs go-librespot and tracks whether it is signed in and ready."""

    daemon = True

    def __init__(self, name: str, data_dir: Path, api_port: int = 3678, note=None) -> None:
        super().__init__(name="player")
        self.device_name = name
        self.dir = Path(data_dir) / "player"
        self.api = f"http://127.0.0.1:{api_port}"
        self.api_port = api_port
        self.hub = AudioHub()
        self.binary = os.environ.get("GO_LIBRESPOT_BIN") or shutil.which("go-librespot")
        self.fifo = Path(tempfile.gettempdir()) / "hubdash-audio.fifo"
        self._note = note or (lambda _text: None)
        self._proc: subprocess.Popen | None = None
        self._id = ""
        # off | starting | pairing | ready | down
        self.state = "off" if not self.enabled else "starting"
        self.device_id: str | None = None
        self.username: str | None = None
        self.pairing: dict | None = None
        self.last_line = ""

    @property
    def enabled(self) -> bool:
        return bool(self.binary) and hasattr(os, "mkfifo")

    @property
    def ready(self) -> bool:
        return self.state == "ready" and bool(self.device_id)

    def info(self) -> dict:
        out = {"state": self.state, "listeners": self.hub.listeners}
        if self.pairing:
            out["pairing"] = self.pairing
        return out

    def debug(self) -> dict:
        return {
            **self.info(),
            "device_id": self.device_id,
            "username": self.username,
            "audio_mb": round(self.hub.bytes_in / 1e6, 1),
            "last_audio_age": round(time.time() - self.hub.last_audio, 1) if self.hub.last_audio else None,
            "last_line": self.last_line,
        }

    # ------------------------------------------------------------- process

    def _own_id(self) -> str:
        """The Spotify device ID of this player: made once, then kept."""
        path = self.dir / "device_id"
        try:
            saved = path.read_text(encoding="utf-8").strip()
            if len(saved) == 40:
                return saved
        except OSError:
            pass
        fresh = os.urandom(20).hex()
        path.write_text(fresh, encoding="utf-8")
        return fresh

    def _write_config(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._id = self._own_id()
        # json.dumps gives a quoted string that is also valid YAML.
        (self.dir / "config.yml").write_text(
            "\n".join(
                [
                    f"device_id: {json.dumps(self._id)}",
                    f"device_name: {json.dumps(self.device_name)}",
                    "device_type: speaker",
                    "audio_backend: pipe",
                    f"audio_output_pipe: {json.dumps(str(self.fifo))}",
                    "audio_output_pipe_format: s16le",
                    "bitrate: 320",
                    "initial_volume: 100",
                    "zeroconf_enabled: false",
                    "log_disable_timestamp: true",
                    "credentials:",
                    "  type: device_auth",
                    "server:",
                    "  enabled: true",
                    "  address: 127.0.0.1",
                    f"  port: {self.api_port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def run(self) -> None:
        if not self.enabled:
            return
        if not self.fifo.exists():
            os.mkfifo(self.fifo)
        # Opened read-write so the pipe always has a reader (the player refuses
        # to start playback otherwise) and never reports end-of-file.
        fd = os.open(self.fifo, os.O_RDWR)
        threading.Thread(
            target=self.hub.pump, args=(lambda n: os.read(fd, n),), name="audio", daemon=True
        ).start()
        threading.Thread(target=self._watch, name="player-watch", daemon=True).start()

        wait = 5
        while True:
            started = time.time()
            try:
                self._write_config()
                self._proc = subprocess.Popen(
                    [self.binary, "--config_dir", str(self.dir)],
                    # It also wants a home folder it can write to.
                    env={**os.environ, "HOME": str(self.dir), "XDG_CONFIG_HOME": str(self.dir),
                         "XDG_CACHE_HOME": str(self.dir / "cache")},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                )
                self._note("player: started")
                for line in self._proc.stdout:
                    line = line.strip()
                    if line:
                        self.last_line = line[:300]
                        log.info("%s", line)
                code = self._proc.wait()
                self._note(f"player: stopped (exit {code}): {self.last_line}")
            except OSError as err:
                self._note(f"player: could not start: {err}")
            self.state, self.device_id, self.pairing = "down", None, None
            wait = 5 if time.time() - started > 60 else min(wait * 2, 60)
            time.sleep(wait)

    # -------------------------------------------------------------- status

    def _get(self, path: str):
        try:
            with urllib.request.urlopen(self.api + path, timeout=4) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as err:
            return err.code, None
        except (OSError, ValueError):
            return 0, None

    def _watch(self) -> None:
        while True:
            time.sleep(3)
            if not self._proc or self._proc.poll() is not None:
                continue
            before = self.state
            status, body = self._get("/")
            if status == 200 and body and body.get("playback_ready"):
                self.device_id = self._id
                self.pairing = None
                self.state = "ready"
                _, session = self._get("/status")
                if session:
                    self.username = session.get("username")
            else:
                self.device_id = None
                code_status, code = self._get("/auth/code")
                if code_status == 200 and code:
                    self.pairing = {"url": code.get("url"), "code": code.get("code")}
                    self.state = "pairing"
                else:
                    self.pairing = None
                    self.state = "starting"
            if self.state != before:
                self._note(f"player: {self.state}")
