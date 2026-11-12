"""The audio fan-out and the player's configuration (no player binary needed)."""
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
        two_seconds = [bytes(mod.CHUNK)] * 20
        hub.pump(reader(two_seconds), clock.sleep, clock.now)
        self.assertAlmostEqual(clock.slept, 2.0 - hub.LEAD, places=3)

    def test_clock_restarts_after_a_silence(self):
        hub = mod.AudioHub()
        clock = FakeClock()
        pieces = [bytes(mod.CHUNK)] * 10

        def read(_n):
            if len(pieces) == 5:
                clock.t += 30  # nothing played for half a minute
            return pieces.pop(0) if pieces else b""

        hub.pump(read, clock.sleep, clock.now)
        # Without the restart, the second half would be sent with no pacing debt
        # carried over; with it, each half is paced on its own.
        self.assertAlmostEqual(clock.slept, 2 * (0.5 - hub.LEAD), places=3)

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
    def make(self, **env):
        return mod.LocalPlayer('Kitchen "Dash"', Path(tempfile.mkdtemp()), 4000)

    def test_config_names_the_device_and_keeps_its_id(self):
        local = self.make()
        local._write_config()
        text = (local.dir / "config.yml").read_text(encoding="utf-8")
        self.assertIn('device_name: "Kitchen \\"Dash\\""', text)
        self.assertIn("audio_backend: pipe", text)
        self.assertIn("  type: device_auth", text)
        self.assertIn("  port: 4000", text)
        first = local._id
        self.assertEqual(len(first), 40)
        local._write_config()
        self.assertEqual(local._id, first)

    def test_without_the_binary_there_is_no_player(self):
        local = self.make()
        local.binary = None
        self.assertFalse(local.enabled)
        self.assertFalse(local.ready)


if __name__ == "__main__":
    unittest.main()
