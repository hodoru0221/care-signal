import unittest

from gateway.model_bridge import normalize
from model.care_signal_contract import care_signal_state


class CSIModelConnectionTests(unittest.TestCase):
    def test_all_classifier_labels_map_to_server_states(self):
        expected = {
            "empty": "EMPTY",
            "still": "IN_BED",
            "moving": "MOVEMENT_ANOMALY",
            "bed_exit": "OUT_OF_BED",
        }
        for label, state in expected.items():
            with self.subTest(label=label):
                self.assertEqual(care_signal_state(label), state)
                observation = normalize(
                    {"label": state, "score": 0.9},
                    room_id="room-01",
                    device_id="csi-receiver-01",
                    model_version="synthetic-v1",
                )
                self.assertEqual(observation["state"], state)

    def test_unknown_classifier_label_is_rejected(self):
        with self.assertRaises(ValueError):
            care_signal_state("unknown")


if __name__ == "__main__":
    unittest.main()
