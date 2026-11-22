"""Clarification is separate from confirmation (and the trigger is not a 'yes')."""
import unittest
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
import stage7_confirmation as s7

CLARIFY = {"status": "needs_clarification", "action": "diagnosis",
           "clarification_message": "I heard 'hemodialysis' and recognised it as a procedure, "
                                    "not a disease. Please say the corrected command."}


def accepted(action, slots=None):
    return {"status": "accepted", "action": action, "slots": slots or {}}


def policy(timeout):
    """Patch the confirmation_timeout_sec of every action to `timeout`."""
    cfg = {a: dict(v, confirmation_timeout_sec=timeout) for a, v in s7._action_cfg().items()}
    return mock.patch.object(s7, "_action_cfg", return_value=cfg)


class Clarification(unittest.TestCase):
    def test_replies_cannot_confirm_a_clarification(self):
        for reply in ("yes", "ok glasses correct", "no", "hmm maybe", None):
            with self.subTest(reply=reply):
                asked = []
                r = s7.run(CLARIFY, "diagnosis", respond=lambda p: asked.append(p) or reply)
                self.assertEqual(r.outcome, "clarification_requested")
                self.assertFalse(r.needs_confirmation)
                self.assertEqual(asked, [])                     # no yes/no question is asked
                self.assertNotIn("confirm", r.final_message.lower())

    def test_no_auto_confirmation_under_any_policy(self):
        for timeout in (None, 5, 0):
            with self.subTest(timeout=timeout), policy(timeout):
                r = s7.run(CLARIFY, "diagnosis", respond=None)
                self.assertEqual(r.outcome, "clarification_requested")
                self.assertNotIn(r.outcome, ("confirmed", "auto_confirmed"))

    def test_rejected_partial_and_inactive_are_not_confirmable(self):
        for status in ("rejected", "accepted_partial", "inactive"):
            with self.subTest(status=status):
                r = s7.run({"status": status, "action": "set_note"}, "set_note", respond=lambda p: "yes")
                self.assertEqual(r.outcome, "not_applicable")


class AcceptedKeepsPolicy(unittest.TestCase):
    def test_explicit_policy(self):
        with policy(None):
            self.assertEqual(s7.run(accepted("diagnosis"), "diagnosis", respond=lambda p: "yes").outcome, "confirmed")
            self.assertEqual(s7.run(accepted("diagnosis"), "diagnosis", respond=lambda p: "wrong").outcome, "rejected")
            self.assertEqual(s7.run(accepted("diagnosis"), "diagnosis", respond=lambda p: "banana").outcome, "unclear")

    def test_timed_policy_auto_confirms_on_silence(self):
        with policy(5):
            r = s7.run(accepted("show"), "show", respond=lambda p: None)
            self.assertEqual(r.outcome, "auto_confirmed")

    def test_immediate_policy(self):
        with policy(0):
            asked = []
            r = s7.run(accepted("emergency"), "emergency", respond=lambda p: asked.append(p))
            self.assertEqual(r.outcome, "auto_confirmed")
            self.assertEqual(asked, [])

    def test_messages_do_not_claim_execution(self):
        with policy(None):
            r = s7.run(accepted("diagnosis"), "diagnosis", respond=lambda p: "correct")
        self.assertNotIn("executed.", r.final_message.replace("nothing was executed", ""))
        self.assertIn("nothing was executed", r.final_message)


class TriggerIsNotAYes(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(s7.classify_confirmation("ok glasses correct"), "confirmed")
        self.assertEqual(s7.classify_confirmation("ok glasses, wrong"), "rejected")
        self.assertEqual(s7.classify_confirmation("ok glasses"), "unclear")
        self.assertEqual(s7.classify_confirmation("ok glasses show heart rate"), "unclear")
        self.assertEqual(s7.classify_confirmation("okay"), "confirmed")   # a plain 'okay' reply still means yes

    def test_punctuated_trigger_is_removed_too(self):
        """Review bug 2: the same trigger variants as detect_trigger(), with punctuation."""
        for reply, verdict in [("ok, glasses", "unclear"), ("Ok-glasses.", "unclear"),
                               ("okay, glasses show heart rate", "unclear"),
                               ("okie... glasses!", "unclear"),
                               ("ok, glasses, correct", "confirmed"), ("OK. Glasses: yes", "confirmed"),
                               ("okay, glasses, wrong", "rejected"), ("ok-glasses no", "rejected")]:
            with self.subTest(reply=reply):
                self.assertEqual(s7.classify_confirmation(reply), verdict)

    def test_strip_trigger_keeps_the_rest(self):
        from stage1_preprocess import strip_trigger, detect_trigger
        self.assertEqual(strip_trigger("ok, glasses, blood pressure 120/80."), "blood pressure 120/80.")
        self.assertEqual(strip_trigger("so OK-glasses show heart rate"), "so show heart rate")
        for text in ("ok, glasses", "Ok-glasses.", "okie... glasses!", "OK. Glasses: yes"):
            self.assertTrue(detect_trigger(text))                 # the two functions agree


if __name__ == "__main__":
    unittest.main()
