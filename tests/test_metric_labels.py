"""Metric display names, same scores, historical files still readable."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT
from metric_labels import ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL, label_counts

REPORTED = ROOT / "evaluation" / "old_runs" / "reports" / "final" / "all_results_2026-11-03.json"


def numbers(obj):
    """All numbers of a report, by path (text such as definitions is ignored)."""
    out = {}

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            out[path] = o
    walk(obj, "")
    return out


class Labels(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ACTION_LABEL, "Action classification accuracy")
        self.assertEqual(ENTITY_LABEL, "Entity extraction accuracy (normalised exact match)")

    def test_denominators_come_from_the_labels(self):
        n = label_counts()
        self.assertEqual((n["commands"], n["valid"], n["invalid"]), (243, 130, 113))
        self.assertEqual((n["action"], n["entity"], n["end_signal"]), (130, 92, 20))

    def test_rebuilt_report_has_identical_numbers_and_new_labels(self):
        """Rebuild the first-run report from the stored run folders into a temporary
        folder: every number must equal the reported file, and the text uses the new names."""
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(ROOT / "evaluation/scripts/build_final_report.py"),
                            "--date", "2026-11-03", "--tags", "keyword-replay",
                            "--baseline", "2026-11-03_B0-ludyga_final500_r1",
                            "--e3", "2026-11-03_E3_entity-resolution", "--out", tmp,
                            "--runs-dir", str(ROOT / "evaluation" / "old_runs" / "runs")],
                           check=True, capture_output=True, cwd=ROOT)
            new = json.loads((Path(tmp) / "all_results_2026-11-03.json").read_text())
            md = (Path(tmp) / "main_results_2026-11-03.md").read_text()
        old = json.loads(REPORTED.read_text())
        self.assertEqual(numbers(new), numbers(old))
        defs = new["_about"]["metrics"]
        self.assertTrue(defs["action_accuracy_pct"].startswith(ACTION_LABEL))
        self.assertTrue(defs["entity_accuracy_pct"].startswith(ENTITY_LABEL))
        for label in (ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL):
            self.assertIn(label, md)
        self.assertNotIn("| action accuracy |", md)

    def test_generators_use_the_shared_names(self):
        for script in ("make_figures.py", "make_extraction_figure.py", "make_report_tables.py",
                       "build_final_report.py"):
            src = (ROOT / "evaluation" / "scripts" / script).read_text()
            with self.subTest(script=script):
                self.assertIn("from metric_labels import", src)
                self.assertNotIn('"Action accuracy"', src)
                self.assertNotIn("'Action accuracy'", src)


if __name__ == "__main__":
    unittest.main()
