"""Finding 11: every segment of a compound medication must resolve with an allowed type.

Uses the local ontology index (no remote calls) and a controlled Stage 2 result."""
import unittest
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
import run_pipeline as rp
import stage4_ontology_lookup as s4
import stage7_confirmation as s7
from stage1_preprocess import preprocess
from stage2_llm_extract import LLMResult
from stage5_type_validate import validate as type_validate

UNKNOWN_A, UNKNOWN_B = "zqxvnonexistent", "qwxzimaginary"


def run_med(entity, action="set_medication"):
    text = f"ok glasses set medication {entity} finish medication"
    stage2 = LLMResult(action=action, entity=entity, end_signal=False, confidence=0.9,
                       raw_response="", model="fake")
    cmd = {"id": "t", "text": text, "valid": None, "expected_action": None,
           "category": "test", "action_source": "test"}
    return rp.validate_extraction(cmd, preprocess(text), stage2)


class CompoundLookup(unittest.TestCase):
    def test_all_drugs_known(self):
        r = run_med("aspirin 100 milligrams and clopidogrel 75 milligrams")
        self.assertTrue(r["frame_valid"], r["frame_reason"])
        self.assertEqual(r["s5_status"], "accepted")
        self.assertEqual(r["s6_status"], "accepted")
        self.assertEqual(len(r["ont_drug_list"]), 2)

    def test_one_unknown_in_either_position(self):
        for entity in (f"aspirin 100 milligrams and {UNKNOWN_A} 50 milligrams",
                       f"{UNKNOWN_A} 50 milligrams and aspirin 100 milligrams"):
            with self.subTest(entity=entity):
                r = run_med(entity)
                self.assertEqual(r["s5_status"], "entity_not_found")
                self.assertEqual(r["s6_status"], "needs_clarification")
                p = r["s6_payload"]
                self.assertEqual(p["unresolved_segments"], [f"{UNKNOWN_A} 50 milligrams"])
                self.assertIn(UNKNOWN_A, p["clarification_message"])
                self.assertIn("Recognised", p["clarification_message"])      # the known drug is kept
                self.assertEqual(sum(d["found"] for d in p["drugs"]), 1)     # per-drug results kept
                self.assertNotIn(UNKNOWN_A, " ".join(str(d["drug_label"]) for d in p["drugs"]))  # no guess

    def test_all_unknown_keeps_per_drug_results(self):
        r = run_med(f"{UNKNOWN_A} 50 milligrams and {UNKNOWN_B} 20 milligrams")
        self.assertEqual(r["s6_status"], "needs_clarification")
        self.assertEqual(len(r["ont_drug_list"]), 2)
        self.assertFalse(any(d.found for d in r["ont_drug_list"]))
        self.assertEqual(len(r["s6_payload"]["unresolved_segments"]), 2)

    def test_three_segments(self):
        ok = run_med("aspirin 100 milligrams and clopidogrel 75 milligrams and omeprazole 20 milligrams")
        self.assertEqual(ok["s6_status"], "accepted", ok["s5_reason"])
        bad = run_med(f"aspirin 100 milligrams and clopidogrel 75 milligrams and {UNKNOWN_A} 5 milligrams")
        self.assertEqual(bad["s6_status"], "needs_clarification")
        self.assertEqual(bad["s6_payload"]["unresolved_segments"], [f"{UNKNOWN_A} 5 milligrams"])

    def test_incompatible_type_of_one_segment(self):
        real = s4._match

        def fake_match(query, index):
            if query.startswith("drugx"):
                return s4.OntologyResult(True, query, "Drug X Measurement", "procedure", "C0", "exact", 1.0, "ncit")
            return real(query, index)
        with mock.patch.object(s4, "_match", side_effect=fake_match):
            r = run_med("aspirin 100 milligrams and drugx 5 milligrams")
        self.assertEqual(r["s5_status"], "type_mismatch")
        self.assertEqual(r["s6_status"], "needs_clarification")
        self.assertEqual(r["s6_payload"]["unresolved_segments"], ["drugx 5 milligrams"])

    def test_single_drug_regression(self):
        ok = run_med("aspirin 100 milligrams")
        self.assertEqual(ok["s6_status"], "accepted")
        self.assertIsNone(ok["ont_drug_list"])
        missing = run_med(f"{UNKNOWN_A} 50 milligrams")
        self.assertEqual(missing["s5_status"], "entity_not_found")
        self.assertIsNone(missing["ont_drug_list"])


class CompoundThroughStage7(unittest.TestCase):
    def test_unresolved_compound_cannot_be_confirmed(self):
        r = run_med(f"aspirin 100 milligrams and {UNKNOWN_A} 50 milligrams")
        for reply in ("yes", "ok glasses correct", None):
            with self.subTest(reply=reply):
                out = s7.run(r["s6_payload"], r["action"], respond=lambda p: reply)
                self.assertEqual(out.outcome, "clarification_requested")

    def test_stage5_directly(self):
        res = s4.lookup("set_medication", f"aspirin 100 milligrams and {UNKNOWN_A} 50 milligrams")
        self.assertFalse(res.found)
        self.assertEqual(type_validate("set_medication", res).status, "entity_not_found")


if __name__ == "__main__":
    unittest.main()
