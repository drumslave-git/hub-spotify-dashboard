"""The audio fan-out and the player's status handling (no Chrome needed)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import player as mod  # noqa: E402


def reader(pieces):
    """A read(n) that hands out the given byte strings, then end-of-stream."""
    pieces = list(pieces)
    return lambda _n: pieces.pop(0) if pieces else b""


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += seconds
        self.t += seconds


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class AudioHubTests(unittest.TestCase):
    def test_only_whole_frames_are_published_in_order(self):
        hub = mod.AudioHub()
        q = hub.subscribe()
        data = bytes(range(200)) * 2  # 400 bytes = 100 frames
        clock = FakeClock()
        hub.pump(reader([data[:3], data[3:10], data[10:399], data[399:]]), clock.sleep, clock.now)
        chunks = drain(q)
        self.assertTrue(all(len(c) % mod.FRAME == 0 for c in chunks))
        self.assertEqual(b"".join(chunks), data)

    def test_audio_is_released_at_playing_speed(self):
        hub = mod.AudioHub()
        clock = FakeClock()
        two_seconds = [bytes([1]) * mod.CHUNK] * 20
        hub.pump(reader(two_seconds), clock.sleep, clock.now)
        self.assertAlmostEqual(clock.slept, 2.0 - hub.LEAD, places=3)

    def test_clock_restarts_after_a_silence(self):
        hub = mod.AudioHub()
        clock = FakeClock()
        pieces = [bytes([1]) * mod.CHUNK] * 10

        def read(_n):
            if len(pieces) == 5:
                clock.t += 30  # nothing played for half a minute
            return pieces.pop(0) if pieces else b""

        hub.pump(read, clock.sleep, clock.now)
        # Without the restart, the second half would be sent with no pacing debt
        # carried over; with it, each half is paced on its own.
        self.assertAlmostEqual(clock.slept, 2 * (0.5 - hub.LEAD), places=3)

    def test_long_silence_is_not_streamed(self):
        hub = mod.AudioHub()
        q = hub.subscribe()
        clock = FakeClock()
        loud = bytes([1]) * mod.CHUNK
        hub.pump(reader([loud] + [bytes(mod.CHUNK)] * 30 + [loud]), clock.sleep, clock.now)
        chunks = drain(q)
        # The sound, a short tail of silence, then the next sound.
        self.assertEqual(len(chunks), 1 + hub.QUIET_CHUNKS + 1)
        self.assertEqual(chunks[-1], loud)

    def test_slow_listener_loses_the_oldest_audio(self):
        hub = mod.AudioHub()
        q = hub.subscribe()
        for i in range(hub.BACKLOG + 3):
            hub.publish(bytes([i]) * 4)
        chunks = drain(q)
        self.assertEqual(len(chunks), hub.BACKLOG)
        self.assertEqual(chunks[-1], bytes([hub.BACKLOG + 2]) * 4)

    def test_unsubscribed_listener_gets_nothing(self):
        hub = mod.AudioHub()
        q = hub.subscribe()
        hub.unsubscribe(q)
        hub.publish(b"\0" * 4)
        self.assertTrue(q.empty())
        self.assertEqual(hub.listeners, 0)

    def test_wav_header(self):
        header = mod.wav_header()
        self.assertEqual(len(header), 44)
        self.assertEqual(header[:4] + header[8:16], b"RIFFWAVEfmt ")
        channels, rate = struct.unpack("<HI", header[22:28])
        self.assertEqual((channels, rate), (2, 44100))


class LocalPlayerTests(unittest.TestCase):
    def make(self):
        notes = []
        local = mod.LocalPlayer("Kitchen & Co", 9000, note=notes.append)
        return local, notes

    def test_player_page_address_carries_the_name(self):
        local, _ = self.make()
        self.assertEqual(local.page, "http://127.0.0.1:9000/engine.html?name=Kitchen%20%26%20Co")

    def test_ready_once_the_page_reports_a_device(self):
        local, notes = self.make()
        local.report({"event": "ready", "device": "abc"})
        self.assertTrue(local.ready)
        self.assertEqual(local.device_id, "abc")
        local.report({"event": "alive", "device": "abc"})
        self.assertEqual(notes, ["player: ready"])  # only changes are noted

    def test_not_ready_and_waiting_are_not_ready(self):
        local, _ = self.make()
        local.report({"event": "ready", "device": "abc"})
        local.report({"event": "not_ready"})
        self.assertFalse(local.ready)
        local.report({"event": "waiting"})
        self.assertEqual(local.state, "waiting")

    def test_errors_get_a_plain_message_and_survive_idle_reports(self):
        local, _ = self.make()
        local.report({"event": "error", "message": "account_error: premium required"})
        self.assertEqual(local.info()["message"], "Spotify Premium is needed to play here")
        local.report({"event": "idle"})
        self.assertEqual(local.state, "error")
        local.report({"event": "error", "message": "initialization_error: nope"})
        self.assertEqual(local.message, "Spotify's player could not start")

    def test_without_chrome_there_is_no_player(self):
        local, _ = self.make()
        local.chrome = None
        self.assertFalse(local.enabled)
        self.assertFalse(local.ready)


if __name__ == "__main__":
    unittest.main()
