"""Tests for the daemon's config and the bench's statistics (stdlib only: python -m unittest)."""

import contextlib
import io
import os
import socket
import threading
import unittest

from config import DEFAULTS, load_config
from atvbench import run, summarize


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = load_config({}, home="/home/pi")
        self.assertEqual(cfg["id"], DEFAULTS["id"])
        self.assertEqual(cfg["host"], DEFAULTS["host"])
        self.assertEqual(cfg["bind"], "0.0.0.0")
        self.assertEqual(cfg["port"], 7777)
        self.assertEqual(cfg["storage"], os.path.join("/home/pi", ".pyatv.conf"))

    def test_overrides(self):
        cfg = load_config({
            "ATVD_ID": "AA:BB",
            "ATVD_HOST": "10.0.0.5",
            "ATVD_BIND": "127.0.0.1",
            "ATVD_PORT": "7000",
            "ATVD_STORAGE": "/data/pyatv.conf",
        }, home="/root")
        self.assertEqual(cfg, {
            "id": "AA:BB", "host": "10.0.0.5", "bind": "127.0.0.1",
            "port": 7000, "storage": "/data/pyatv.conf",
        })

    def test_empty_value_means_default(self):
        cfg = load_config({"ATVD_PORT": "", "ATVD_HOST": "  "}, home="/root")
        self.assertEqual(cfg["port"], 7777)
        self.assertEqual(cfg["host"], DEFAULTS["host"])

    def test_bad_port(self):
        for bad in ("abc", "0", "70000"):
            with self.assertRaises(ValueError):
                load_config({"ATVD_PORT": bad}, home="/root")


class SummarizeTests(unittest.TestCase):
    def test_stats(self):
        s = summarize([5.0, 1.0, 3.0, 2.0, 4.0], sent=6)
        self.assertEqual(s["sent"], 6)
        self.assertEqual(s["received"], 5)
        self.assertEqual(s["lost"], 1)
        self.assertEqual(s["min"], 1.0)
        self.assertEqual(s["median"], 3.0)
        self.assertEqual(s["max"], 5.0)
        self.assertEqual(s["p95"], 5.0)

    def test_even_median(self):
        self.assertEqual(summarize([1.0, 2.0, 3.0, 4.0], sent=4)["median"], 2.5)

    def test_p95_nearest_rank(self):
        times = [float(i) for i in range(1, 101)]   # 1..100
        self.assertEqual(summarize(times, sent=100)["p95"], 95.0)

    def test_nothing_received(self):
        s = summarize([], sent=3)
        self.assertEqual(s["received"], 0)
        self.assertEqual(s["lost"], 3)
        self.assertIsNone(s["median"])


class RunTests(unittest.TestCase):
    def test_round_trips_against_a_responder(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        seen = []

        def answer():
            for _ in range(3):
                data, addr = server.recvfrom(2048)
                seen.append(data)
                server.sendto(b"pong", addr)

        t = threading.Thread(target=answer, daemon=True)
        t.start()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                times = run("127.0.0.1", port, "ping", 3, 2.0, 0)
        finally:
            t.join(2)
            server.close()
        self.assertEqual(seen, [b"ping"] * 3)
        self.assertEqual(len(times), 3)
        self.assertTrue(all(ms >= 0 for ms in times))


if __name__ == "__main__":
    unittest.main()
