"""Spotify's full web app, shown on the Hub as a picture of a screen.

Chrome on the server (the same one that runs the dashboard's own player)
opens open.spotify.com in a second tab. That tab's screen is sent to the page
as a stream of JPEG frames, and taps, swipes and typing from the page are
played back into the tab as mouse and keyboard input. The app's sound comes
out of the same virtual sound card as the player's, so it reaches the Hub
through /api/audio like all other music.

Chrome is driven over its DevTools protocol on a pair of pipes
(--remote-debugging-pipe), so no debugging port is opened on the network.
"""
from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import struct
import threading
import time

log = logging.getLogger("screen")

APP_URL = "https://open.spotify.com/"
WIDTH, HEIGHT = 976, 600   # the Hub's 1024 x 600, less the page's own button strip


class CdpError(Exception):
    pass


class Cdp:
    """Chrome's DevTools protocol over --remote-debugging-pipe: JSON messages,
    each ended by a NUL byte, Chrome reads on fd 3 and writes on fd 4."""

    def __init__(self, to_chrome: int, from_chrome: int) -> None:
        self._out = to_chrome
        self._in = from_chrome
        self._ids = itertools.count(1)
        self._waiting: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._handlers: list = []
        self.closed = False
        threading.Thread(target=self._read, name="cdp", daemon=True).start()

    def on_event(self, handler) -> None:
        """handler(method, params, session_id) for every event Chrome sends."""
        self._handlers.append(handler)

    def _write(self, msg_id: int, method: str, params: dict | None, session: str | None) -> None:
        if self.closed:
            raise CdpError("Chrome has gone")
        msg = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        data = json.dumps(msg).encode() + b"\0"
        try:
            with self._send_lock:
                while data:
                    data = data[os.write(self._out, data):]
        except OSError as err:
            raise CdpError(f"cannot reach Chrome ({err})") from err

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 15) -> dict:
        msg_id = next(self._ids)
        slot = {"done": threading.Event()}
        with self._lock:
            self._waiting[msg_id] = slot
        try:
            self._write(msg_id, method, params, session)
        except CdpError:
            with self._lock:
                self._waiting.pop(msg_id, None)
            raise
        if not slot["done"].wait(timeout):
            with self._lock:
                self._waiting.pop(msg_id, None)
            raise CdpError(f"{method}: no answer from Chrome")
        if "error" in slot:
            raise CdpError(f"{method}: {slot['error'].get('message')}")
        return slot.get("result") or {}

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> None:
        """Fire and forget, in order (for input, where waiting would only add
        lag). Chrome's answer is dropped when it arrives."""
        try:
            self._write(next(self._ids), method, params, session)
        except CdpError as err:
            log.debug("%s", err)

    def _read(self) -> None:
        buffer = b""
        try:
            while True:
                data = os.read(self._in, 1 << 20)
                if not data:
                    break
                buffer += data
                while b"\0" in buffer:
                    raw, buffer = buffer.split(b"\0", 1)
                    try:
                        self._dispatch(json.loads(raw))
                    except ValueError:
                        continue
        except OSError:
            pass
        self.closed = True
        with self._lock:
            waiting, self._waiting = self._waiting, {}
        for slot in waiting.values():
            slot["error"] = {"message": "Chrome has gone"}
            slot["done"].set()

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg:
            with self._lock:
                slot = self._waiting.pop(msg["id"], None)
            if slot is not None:
                if "error" in msg:
                    slot["error"] = msg["error"]
                else:
                    slot["result"] = msg.get("result")
                slot["done"].set()
            return
        for handler in self._handlers:
            try:
                handler(msg.get("method"), msg.get("params") or {}, msg.get("sessionId"))
            except Exception:  # a broken handler must not stop the reader
                log.exception("CDP event handler failed")


# Keys the page may send by name, with the codes Chrome needs to act on them.
KEYS = {
    "Backspace": 8, "Tab": 9, "Enter": 13, "Escape": 27, "Space": 32,
    "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40, "Delete": 46,
}


class AppScreen:
    """The Spotify app tab: its frames out, input in."""

    def __init__(self, url: str = APP_URL) -> None:
        self.url = url
        self.cdp: Cdp | None = None
        self.session: str | None = None
        self.state = "off"           # off | starting | ready | error
        self.message = ""
        self._frame: bytes | None = None
        self._frame_no = 0
        self._cond = threading.Condition()
        self._viewers = 0
        self._casting = False
        self._lock = threading.Lock()
        self.frames_in = 0
        self.last_input = 0.0
        self.title = ""

    # -------------------------------------------------------------- lifecycle

    def attach(self, cdp: Cdp) -> None:
        """Called by the player once Chrome is up: open the app tab."""
        self.cdp, self.session, self._casting = cdp, None, False
        self.state, self.message = "starting", ""
        cdp.on_event(self._event)
        try:
            version = cdp.call("Browser.getVersion")
            target = cdp.call("Target.createTarget", {"url": "about:blank"})
            session = cdp.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})["sessionId"]
            # Headless Chrome names itself in its user agent, and Spotify then
            # calls the browser unsupported. Present it as the Chrome it is.
            agent = version.get("userAgent", "").replace("HeadlessChrome", "Chrome")
            cdp.call("Network.setUserAgentOverride", {"userAgent": agent}, session)
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False}, session)
            cdp.call("Page.enable", {}, session)
            cdp.call("Page.navigate", {"url": self.url}, session)
            self.session = session
            self.state = "ready"
            log.info("app tab open")
        except CdpError as err:
            self.state, self.message = "error", str(err)
            log.warning("app tab: %s", err)
            return
        if self._viewers:
            self._start_cast()

    def detach(self) -> None:
        """Chrome has stopped (it will be restarted)."""
        self.cdp, self.session, self._casting = None, None, False
        if self.state != "off":
            self.state = "starting"

    def info(self) -> dict:
        out = {"state": self.state, "width": WIDTH, "height": HEIGHT}
        if self.message:
            out["message"] = self.message
        return out

    def debug(self) -> dict:
        return {
            **self.info(),
            "viewers": self._viewers,
            "casting": self._casting,
            "frames": self.frames_in,
            "title": self.title,
        }

    # ---------------------------------------------------------------- frames

    def _event(self, method: str, params: dict, session: str | None) -> None:
        if session != self.session or not self.session:
            return
        if method == "Page.screencastFrame":
            # Acknowledge at once: Chrome sends the next frame only after that.
            self.cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")}, self.session)
            frame = base64.b64decode(params.get("data") or "")
            with self._cond:
                self._frame = frame
                self._frame_no += 1
                self.frames_in += 1
                self._cond.notify_all()
        elif method == "Page.frameNavigated" and not (params.get("frame") or {}).get("parentId"):
            self.title = (params.get("frame") or {}).get("url", "")
            # A new document ends the screencast; start it again for whoever
            # watches. (Not from here: this runs on the thread that reads
            # Chrome's answers, so waiting for one would wait forever.)
            self._casting = False
            if self._viewers:
                threading.Thread(target=self._start_cast, daemon=True).start()

    def _start_cast(self) -> None:
        with self._lock:
            if self._casting or not self.session or not self.cdp:
                return
            self._casting = True
        try:
            self.cdp.call("Page.startScreencast",
                          {"format": "jpeg", "quality": 75, "maxWidth": WIDTH, "maxHeight": HEIGHT,
                           "everyNthFrame": 1}, self.session)
        except CdpError as err:
            self._casting = False
            log.warning("screencast: %s", err)

    def _stop_cast(self) -> None:
        with self._lock:
            if not self._casting or not self.session or not self.cdp:
                self._casting = False
                return
            self._casting = False
        self.cdp.send("Page.stopScreencast", {}, self.session)

    def watch(self, write, alive=lambda: True) -> None:
        """Send frames to one viewer with write(bytes) until it fails. Each
        frame goes out as a 4-byte big-endian length and the JPEG itself; an
        empty frame (length 0) every few seconds only keeps the line open."""
        with self._cond:
            self._viewers += 1
        self._start_cast()
        seen = -1
        try:
            # A fresh viewer gets a picture at once, rather than waiting for
            # the screen to change.
            if self.cdp and self.session:
                self._refresh()
            while alive():
                with self._cond:
                    if self._frame_no == seen:
                        self._cond.wait(5)
                    frame, number = self._frame, self._frame_no
                fresh = number != seen
                seen = number
                if fresh and frame:
                    write(struct.pack(">I", len(frame)) + frame)
                else:
                    write(struct.pack(">I", 0))
                    if not self._casting:
                        self._start_cast()   # Chrome was restarted, or the start failed
        finally:
            with self._cond:
                self._viewers -= 1
                last = self._viewers == 0
            if last:
                self._stop_cast()

    def _refresh(self) -> None:
        """Ask for one fresh frame (a still screen sends none by itself)."""
        def run():
            try:
                shot = self.cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": 75}, self.session)
                frame = base64.b64decode(shot.get("data") or "")
                if not frame:
                    return
                with self._cond:
                    self._frame = frame
                    self._frame_no += 1
                    self._cond.notify_all()
            except (CdpError, AttributeError):
                pass
        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------------- input

    def input(self, event: dict) -> bool:
        """One input event from the page, in app pixels. Returns False when
        the app is not there to take it."""
        cdp, session = self.cdp, self.session
        if not cdp or not session:
            return False
        self.last_input = time.time()
        kind = str(event.get("type") or "")
        x = max(0.0, min(WIDTH - 1.0, float(event.get("x") or 0)))
        y = max(0.0, min(HEIGHT - 1.0, float(event.get("y") or 0)))

        def mouse(kind_, **extra):
            cdp.send("Input.dispatchMouseEvent", {"type": kind_, "x": x, "y": y, **extra}, session)

        if kind == "move":
            mouse("mouseMoved")
        elif kind in ("tap", "right"):
            button = "right" if kind == "right" else "left"
            mouse("mouseMoved")
            mouse("mousePressed", button=button, buttons=1 if button == "left" else 2, clickCount=1)
            mouse("mouseReleased", button=button, buttons=0, clickCount=1)
        elif kind == "down":
            mouse("mouseMoved")
            mouse("mousePressed", button="left", buttons=1, clickCount=1)
        elif kind == "drag":
            mouse("mouseMoved", button="left", buttons=1)
        elif kind == "up":
            mouse("mouseReleased", button="left", buttons=0, clickCount=1)
        elif kind == "wheel":
            dx = max(-3000.0, min(3000.0, float(event.get("dx") or 0)))
            dy = max(-3000.0, min(3000.0, float(event.get("dy") or 0)))
            mouse("mouseWheel", deltaX=dx, deltaY=dy)
        elif kind == "text":
            text = str(event.get("text") or "")[:500]
            if text:
                cdp.send("Input.insertText", {"text": text}, session)
        elif kind == "key":
            name = str(event.get("key") or "")
            code = KEYS.get(name)
            if code is None:
                return True
            key = " " if name == "Space" else name
            common = {"key": key, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
            text = {"Enter": "\r", "Space": " "}.get(name)
            down = {**common, "type": "keyDown" if text else "rawKeyDown"}
            if text:
                down["text"] = text
            cdp.send("Input.dispatchKeyEvent", down, session)
            cdp.send("Input.dispatchKeyEvent", {**common, "type": "keyUp"}, session)
        elif kind == "nav":
            where = str(event.get("to") or "")
            if where == "back":
                cdp.send("Runtime.evaluate", {"expression": "history.back()"}, session)
            elif where == "home":
                cdp.send("Page.navigate", {"url": self.url}, session)
            elif where == "reload":
                cdp.send("Page.reload", {}, session)
        return True

    def typing(self) -> bool | None:
        """Whether the app's focus is in a text field (after a tap: the page
        then brings up its keyboard). None when that cannot be told."""
        cdp, session = self.cdp, self.session
        if not cdp or not session:
            return None
        try:
            res = cdp.call("Runtime.evaluate", {"returnByValue": True, "expression":
                           "(function(e){return !!e && (e.isContentEditable || "
                           "/^(INPUT|TEXTAREA)$/.test(e.tagName))})(document.activeElement)"},
                           session, timeout=2)
        except CdpError:
            return None
        return bool((res.get("result") or {}).get("value"))
