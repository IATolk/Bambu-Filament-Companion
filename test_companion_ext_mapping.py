import sys, json, copy, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from companion import extract_usage

class TestEXTMapping(unittest.TestCase):
    def make_task(self):
        return {
            "amsMapping": [-1] * 5,
            "amsMapping2": [{"amsId":255,"slotId":255}, {"amsId":255,"slotId":255},
                            {"amsId":255,"slotId":0}, {"amsId":255,"slotId":255},
                            {"amsId":255,"slotId":255}],
            "amsDetailMapping": [{"ams":255,"weight":174.57,"filamentId":"GFG99",
                                  "filamentType":"PETG","targetColor":"FFCC99FF"}],
            "weight":174.57
        }

    def test_ext_valid_single_source(self):
        usage, warn = extract_usage(self.make_task())
        self.assertEqual(len(usage), 1)
        self.assertEqual((usage[0]["ams"],usage[0]["slot"]), (255,0))
        self.assertAlmostEqual(usage[0]["grams"],174.57)
        self.assertTrue(usage[0]["color"].upper().endswith("FFCC99"))

    def test_ambiguous_mapping_refused(self):
        t = self.make_task()
        t["amsMapping2"][0] = {"amsId":255,"slotId":0}
        usage, warn = extract_usage(t)
        self.assertEqual(usage, [])

    def test_unmapped_only_refused(self):
        t = self.make_task()
        t["amsMapping2"][2] = {"amsId":255,"slotId":255}
        usage, warn = extract_usage(t)
        self.assertEqual(usage, [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
