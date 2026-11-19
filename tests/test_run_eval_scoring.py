"""Review bug 5: the evaluation scorer must handle ignored (inactive) input."""
import unittest

import helpers  # noqa: F401  (sets sys.path)
import run_eval as ev
import run_pipeline as rp


def row(valid, category, text="show heart rate"):
    r = rp._inactive_result({"id": f"{category}-{valid}", "text": text, "valid": valid,
                             "expected_action": "show" if valid else None, "category": category})
    return {"id": r["id"], "category": category, "valid": valid,
            "expected_action": r["expected_action"], "text": text,
            "gold": {"entity": "heart rate", "end_signal": None} if valid else {},
            "outcome": ev.OUTCOME_OF_STATUS[r["s6_status"]], "llm_called": False,
            "raw_llm_response": "", "timings": {"stage1": 0.0, "end_to_end": 0.0}, "result": r}


class InactiveScoring(unittest.TestCase):
    def test_inactive_rows_are_scored_without_credit(self):
        rows = [row(True, "valid_show"), row(False, "invalid_ood")]
        s = ev.score_pipeline(rows, rp._action_cfg())       # must not raise KeyError: 'IGNORED'
        for system in ("hybrid", "llm_only_B2"):
            with self.subTest(system=system):
                m = s[system]
                self.assertEqual(m["accuracy_strict_pct"], 0.0)
                self.assertEqual((m["valid_ignored"], m["invalid_ignored"]), (1, 1))
                self.assertEqual(m["valid_accepted"] + m["invalid_rejected"] + m["invalid_clarified"], 0)
        self.assertEqual(s["first_rejecting_stage"]["gold_valid"], {"stage1_no_trigger": 1})
        self.assertEqual(s["first_rejecting_stage"]["gold_invalid"], {"stage1_no_trigger": 1})
        self.assertEqual(s["per_category"]["valid_show"]["IGNORED"], 1)
        self.assertEqual(s["action_accuracy_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
