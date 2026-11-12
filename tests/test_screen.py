"""The DevTools pipe and the Spotify app tab, against a fake Chrome."""
import json
import os
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import screen as mod  # noqa: E402


class FakeChrome(threading.Thread):
    """Reads DevTools commands from one pipe and answers on the other, like
    Chrome does with --remote-debugging-pipe."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.cmd_read, self.cmd_write = os.pipe()
        self.ans_read, self.ans_write = os.pipe()
        self.received = []
        self.answers = {
            "Browser.getVersion": {"userAgent": "Mozilla/5.0 HeadlessChrome/157.0.0.0 Safari/537.36"},
            "Target.createTarget": {"targetId": "T1"},
            "Target.attachToTarget": {"sessionId": "S1"},
            "Runtime.evaluate": {"result": {"type": "boolean", "value": True}},
        }
        self.fail = set()

    def run(self):
        buf = b""
        while True:
            data = os.read(self.cmd_read, 65536)
            if not data:
                return
            buf += data
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                msg = json.loads(raw)
                self.received.append(msg)
                if msg["method"] in self.fail:
                    self.emit({"id": msg["id"], "error": {"message": "nope"}})
                else:
                    self.emit({"id": msg["id"], "result": self.answers.get(msg["method"], {})})

    def emit(self, msg):
        os.write(self.ans_write, json.dumps(msg).encode() + b"\0")

    def methods(self):
        return [m["method"] for m in self.received]

    def wait_for(self, count):
        deadline = time.time() + 2
        while len(self.received) < count and time.time() < deadline:
            time.sleep(0.01)


class CdpTest(unittest.TestCase):
    def setUp(self):
        self.chrome = FakeChrome()
        self.chrome.start()
        self.cdp = mod.Cdp(self.chrome.cmd_write, self.chrome.ans_read)

    def test_call_returns_the_result(self):
        self.assertEqual(self.cdp.call("Target.createTarget"), {"targetId": "T1"})

    def test_error_is_raised(self):
        self.chrome.fail.add("Page.navigate")
        with self.assertRaises(mod.CdpError):
            self.cdp.call("Page.navigate", {"url": "x"})

    def test_events_reach_handlers(self):
        seen = []
        self.cdp.on_event(lambda method, params, session: seen.append((method, params, session)))
        self.chrome.emit({"method": "Page.loadEventFired", "params": {"t": 1}, "sessionId": "S1"})
        deadline = time.time() + 2
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(seen, [("Page.loadEventFired", {"t": 1}, "S1")])

    def test_session_is_sent(self):
        self.cdp.call("Page.enable", {}, "S1")
        self.assertEqual(self.chrome.received[-1]["sessionId"], "S1")

    def test_call_after_chrome_went_fails(self):
        os.close(self.chrome.ans_write)   # Chrome's answers end
        deadline = time.time() + 2
        while not self.cdp.closed and time.time() < deadline:
            time.sleep(0.01)
        with self.assertRaises(mod.CdpError):
            self.cdp.call("Page.enable")


class AppScreenTest(unittest.TestCase):
    def setUp(self):
        self.chrome = FakeChrome()
        self.chrome.start()
        self.app = mod.AppScreen()
        self.app.attach(mod.Cdp(self.chrome.cmd_write, self.chrome.ans_read))

    def sent_after(self, start):
        return self.chrome.received[start:]

    def test_attach_opens_spotify_as_plain_chrome(self):
        self.assertEqual(self.app.state, "ready")
        ua = next(m for m in self.chrome.received if m["method"] == "Network.setUserAgentOverride")
        self.assertNotIn("Headless", ua["params"]["userAgent"])
        nav = next(m for m in self.chrome.received if m["method"] == "Page.navigate")
        self.assertEqual(nav["params"]["url"], mod.APP_URL)
        self.assertEqual(nav["sessionId"], "S1")

    def test_failed_attach_reports_an_error(self):
        chrome = FakeChrome()
        chrome.fail.add("Target.createTarget")
        chrome.start()
        app = mod.AppScreen()
        app.attach(mod.Cdp(chrome.cmd_write, chrome.ans_read))
        self.assertEqual(app.state, "error")

    def test_tap_is_a_click_in_order(self):
        start = len(self.chrome.received)
        self.assertTrue(self.app.input({"type": "tap", "x": 100, "y": 50}))
        self.chrome.wait_for(start + 3)
        events = [(m["params"]["type"], m["params"]["x"], m["params"]["y"]) for m in self.sent_after(start)]
        self.assertEqual(events, [("mouseMoved", 100, 50), ("mousePressed", 100, 50), ("mouseReleased", 100, 50)])

    def test_points_are_kept_on_screen(self):
        start = len(self.chrome.received)
        self.app.input({"type": "move", "x": -40, "y": 99999})
        self.chrome.wait_for(start + 1)
        params = self.sent_after(start)[0]["params"]
        self.assertEqual((params["x"], params["y"]), (0, mod.HEIGHT - 1))

    def test_wheel(self):
        start = len(self.chrome.received)
        self.app.input({"type": "wheel", "x": 10, "y": 10, "dx": 0, "dy": 120})
        self.chrome.wait_for(start + 1)
        params = self.sent_after(start)[0]["params"]
        self.assertEqual((params["type"], params["deltaY"]), ("mouseWheel", 120))

    def test_text_and_keys(self):
        start = len(self.chrome.received)
        self.app.input({"type": "text", "text": "massive"})
        self.app.input({"type": "key", "key": "Enter"})
        self.app.input({"type": "key", "key": "F13"})   # not one we send: ignored
        self.chrome.wait_for(start + 3)
        sent = self.sent_after(start)
        self.assertEqual(sent[0]["method"], "Input.insertText")
        self.assertEqual(sent[0]["params"]["text"], "massive")
        self.assertEqual([m["params"]["type"] for m in sent[1:]], ["keyDown", "keyUp"])
        self.assertEqual(sent[1]["params"]["windowsVirtualKeyCode"], 13)
        self.assertEqual(len(sent), 3)

    def test_no_input_without_chrome(self):
        self.app.detach()
        self.assertFalse(self.app.input({"type": "tap", "x": 1, "y": 1}))
        self.assertIsNone(self.app.typing())

    def test_typing(self):
        self.assertTrue(self.app.typing())

    def test_frames_are_sent_with_their_length(self):
        out = []
        stop = threading.Event()

        def write(data):
            out.append(data)
            if len(data) > 4:
                stop.set()

        viewer = threading.Thread(target=self.app.watch, args=(write, lambda: not stop.is_set()), daemon=True)
        viewer.start()
        self.chrome.wait_for(len(self.chrome.received) + 1)
        self.chrome.emit({"method": "Page.screencastFrame", "sessionId": "S1",
                          "params": {"data": "/9j/AA==", "sessionId": 7}})
        viewer.join(7)
        frames = [d for d in out if len(d) > 4]
        self.assertTrue(frames)
        self.assertEqual(struct.unpack(">I", frames[0][:4])[0], len(frames[0]) - 4)
        self.assertIn("Page.startScreencast", self.chrome.methods())
        self.assertIn("Page.screencastFrameAck", self.chrome.methods())
        # The last viewer gone: the screencast stops.
        self.chrome.wait_for(len(self.chrome.received) + 1)
        deadline = time.time() + 2
        while "Page.stopScreencast" not in self.chrome.methods() and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn("Page.stopScreencast", self.chrome.methods())


if __name__ == "__main__":
    unittest.main()
