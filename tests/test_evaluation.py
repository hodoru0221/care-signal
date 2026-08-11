import unittest

from tools.evaluate_model import evaluate_records


class ModelEvaluationTests(unittest.TestCase):
    def test_metrics_and_latency_are_calculated(self):
        records = [
            {
                "ground_truth": "IN_BED",
                "state": "IN_BED",
                "captured_at": "2026-08-09T00:00:00Z",
                "received_at": "2026-08-09T00:00:00.100Z",
            },
            {
                "ground_truth": "OUT_OF_BED",
                "state": "IN_BED",
                "captured_at": "2026-08-09T00:00:01Z",
                "received_at": "2026-08-09T00:00:01.300Z",
            },
            {"ground_truth": "OUT_OF_BED", "state": "OUT_OF_BED"},
            {"ground_truth": "MOVEMENT_ANOMALY", "state": "MOVEMENT_ANOMALY"},
        ]
        report = evaluate_records(records)
        self.assertEqual(report["samples"], 4)
        self.assertEqual(report["accuracy"], 0.75)
        self.assertEqual(report["confusion_matrix"]["OUT_OF_BED"]["IN_BED"], 1)
        self.assertEqual(report["latency_ms"]["median"], 200.0)
        self.assertEqual(report["latency_ms"]["p95"], 300.0)

    def test_invalid_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_records([{"ground_truth": "FALL", "state": "MOVEMENT_ANOMALY"}])

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_records([])


if __name__ == "__main__":
    unittest.main()
