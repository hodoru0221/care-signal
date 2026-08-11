import json
import tempfile
import unittest
from pathlib import Path

from gateway.model_bridge import bridge_lines, normalize
from gateway.uploader import JsonlTail, enqueue, ensure_observation_id, flush


class ModelBridgeTests(unittest.TestCase):
    def test_alias_and_contract_normalization_are_deterministic(self):
        raw = {"label": "FALL", "score": 0.91, "timestamp": "2026-08-09T01:02:03+09:00",
               "sequence": 7}
        defaults = {"room_id": "room-01", "device_id": "radar-01", "model_version": "fall-v3"}
        first = normalize(raw, **defaults)
        second = normalize(raw, **defaults)
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "MOVEMENT_ANOMALY")
        self.assertEqual(first["captured_at"], "2026-08-08T16:02:03Z")
        self.assertEqual(first["sequence_no"], 7)
        self.assertTrue(first["observation_id"].startswith("obs-"))

    def test_bridge_appends_valid_and_quarantines_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            output, dead = Path(directory) / "inference.jsonl", Path(directory) / "dead.jsonl"
            lines = ['{"state":"IN_BED","confidence":0.8,"captured_at":"2026-01-01T00:00:00Z"}\n',
                     '{broken\n']
            result = bridge_lines(lines, output, dead, room_id="r1", device_id="d1", model_version="v1")
            self.assertEqual(result, (1, 1))
            self.assertEqual(json.loads(output.read_text())["state"], "IN_BED")
            self.assertIn("{broken", json.loads(dead.read_text())["line"])


class UploaderTests(unittest.TestCase):
    def test_missing_observation_id_is_stable(self):
        payload = {"room_id": "r", "device_id": "d", "state": "EMPTY", "confidence": .9,
                   "captured_at": "2026-01-01T00:00:00Z", "model_version": "v", "sequence_no": 1}
        self.assertEqual(ensure_observation_id(payload), ensure_observation_id(payload))

    def test_spool_deduplicates_and_preserves_failed_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "spool.jsonl"
            one = {"observation_id": "one", "room_id": "r", "state": "EMPTY"}
            two = {"observation_id": "two", "room_id": "r", "state": "IN_BED"}
            self.assertTrue(enqueue(spool, one))
            self.assertFalse(enqueue(spool, one))
            self.assertTrue(enqueue(spool, two))
            calls = []

            def failing(_url, _key, payload):
                calls.append(payload["observation_id"])
                raise OSError("offline")

            self.assertEqual(flush("url", "secret", spool, send=failing), 0)
            self.assertEqual(calls, ["one"])
            self.assertEqual([json.loads(line)["observation_id"] for line in spool.read_text().splitlines()],
                             ["one", "two"])

            sent = []
            self.assertEqual(flush("url", "secret", spool,
                                   send=lambda _u, _k, p: sent.append(p["observation_id"])), 2)
            self.assertEqual(sent, ["one", "two"])
            self.assertEqual(spool.read_text(), "")

    def test_malformed_spool_line_goes_to_dead_letter(self):
        with tempfile.TemporaryDirectory() as directory:
            spool, dead = Path(directory) / "spool", Path(directory) / "dead"
            spool.write_text('{bad\n{"observation_id":"ok","room_id":"r","state":"EMPTY"}\n')
            sent = []
            self.assertEqual(flush("url", "secret", spool, dead,
                                   send=lambda _u, _k, p: sent.append(p)), 1)
            self.assertEqual(sent[0]["observation_id"], "ok")
            self.assertIn("{bad", json.loads(dead.read_text())["line"])

    def test_tail_handles_partial_line_truncate_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.jsonl"
            source.write_text("one\npartial", encoding="utf-8")
            tail = JsonlTail(source)
            self.assertEqual(tail.read(), ["one"])
            with source.open("a", encoding="utf-8") as stream:
                stream.write("-done\n")
            self.assertEqual(tail.read(), ["partial-done"])
            source.write_text("new\n", encoding="utf-8")
            self.assertEqual(tail.read(), ["new"])
            rotated = source.with_suffix(".old")
            source.replace(rotated)
            source.write_text("rotated\n", encoding="utf-8")
            self.assertEqual(tail.read(), ["rotated"])


if __name__ == "__main__":
    unittest.main()
