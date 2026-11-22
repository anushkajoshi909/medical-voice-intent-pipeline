"""A new command needs the trigger phrase before any processing."""
import unittest
from unittest import mock

from helpers import FakeClient, answer
import run_pipeline as rp
from stage1_preprocess import detect_trigger, preprocess

M = "fake-model"


def cmd(text):
    return {"id": "t", "text": text, "valid": None, "expected_action": None,
            "category": "test", "action_source": "test"}


class TriggerDetection(unittest.TestCase):
    def test_supported_variants_and_case(self):
        for text in ("ok glasses show bp", "OK Glasses show bp", "okay glasses show bp",
                     "okie glasses show bp", "Ok, glasses, show bp", "ok   glasses show bp"):
            with self.subTest(text=text):
                self.assertTrue(detect_trigger(text))

    def test_missing_or_wrong_trigger(self):
        for text in ("start HD", "glasses start HD", "ok start HD", "hey glasses start HD",
                     "okglasses start HD", ""):
            with self.subTest(text=text):
                self.assertFalse(detect_trigger(text))

    def test_text_before_the_trigger(self):
        self.assertTrue(detect_trigger("um so ok glasses start HD"))
        self.assertEqual(preprocess("um so ok glasses start HD").clean_text, "start hemodialysis")


class Gate(unittest.TestCase):
    def test_missing_trigger_runs_nothing(self):
        client = FakeClient(answer("start", "hemodialysis"))
        with mock.patch.object(rp, "preprocess", wraps=rp.preprocess) as pre, \
             mock.patch.object(rp, "frame_validate") as s3, \
             mock.patch.object(rp, "ontology_lookup") as s4, \
             mock.patch.object(rp, "type_validate") as s5, \
             mock.patch.object(rp, "assemble_intent") as s6:
            r = rp.run_command(client, cmd("start HD"), M)
        self.assertEqual(client.calls, [])                       # zero extraction calls
        for spy in (pre, s3, s4, s5, s6):
            spy.assert_not_called()
        self.assertEqual(r["s6_status"], "inactive")
        self.assertEqual(r["s6_payload"]["reason"], "trigger_not_found")
        self.assertFalse(r["action_correct"])       # scored as IGNORED: test_run_eval_scoring

    def test_inactive_result_has_the_normal_keys(self):
        client = FakeClient(answer("show", "heart rate"))
        normal = rp.run_command(client, cmd("ok glasses show heart rate"), M)
        inactive = rp.run_command(FakeClient(), cmd("show heart rate"), M)
        self.assertEqual(set(normal), set(inactive))

    def test_inactive_never_confirmed(self):
        r = rp.run_command(FakeClient(), cmd("start HD"), M)
        s7 = rp.confirm_intent(r["s6_payload"], r["action"], respond=lambda p: "yes")
        self.assertEqual(s7.outcome, "not_applicable")

    def test_with_trigger_processes_normally(self):
        client = FakeClient(answer("start", "hemodialysis"))
        r = rp.run_command(client, cmd("ok glasses start HD"), M)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["messages"][1]["content"], "start hemodialysis")
        self.assertNotEqual(r["s6_status"], "inactive")
        self.assertTrue(r["trigger_found"])

    def test_benchmark_preparation(self):
        c = cmd("show heart rate")
        prepared = rp.with_trigger(c)
        self.assertEqual(prepared["text"], "ok glasses show heart rate")
        self.assertEqual(c["text"], "show heart rate")          # dataset entry unchanged
        self.assertIs(rp.with_trigger(prepared), prepared)       # no second prefix


if __name__ == "__main__":
    unittest.main()
