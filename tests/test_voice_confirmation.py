"""Voice mode: a timed confirmation is confirmed automatically if Enter is not pressed in time.
Fake Enter key and fake recording: no microphone, no real waiting."""
import unittest
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
import run_pipeline as rp
import stage7_confirmation as s7

ACCEPTED_SHOW = {"status": "accepted", "action": "show",
                 "slots": {"vital_sign": {"canonical": "Heart Rate"}}}
ACCEPTED_DIAGNOSIS = {"status": "accepted", "action": "diagnosis",
                      "slots": {"disease": {"canonical": "pneumonia"}}}


def policy(timeout):
    cfg = {a: dict(v, confirmation_timeout_sec=timeout) for a, v in s7._action_cfg().items()}
    return mock.patch.object(s7, "_action_cfg", return_value=cfg)


class FakeVoice:
    """enter: what read_answer returns ("" = Enter pressed, None = deadline passed).
    transcript: what the fake recording returns."""
    def __init__(self, enter, transcript="yes"):
        self.enter, self.transcript = enter, transcript
        self.timeouts, self.recordings = [], 0

    def read_answer(self, timeout, prompt):
        self.timeouts.append(timeout)
        return self.enter

    def record(self):
        self.recordings += 1
        return self.transcript

    def run(self, payload, action):
        return s7.run(payload, action, read_response=rp.voice_answer(self.read_answer, self.record))


class TimedPolicy(unittest.TestCase):
    def test_no_enter_in_time_confirms_automatically(self):
        v = FakeVoice(enter=None)
        with policy(5), mock.patch("builtins.print"):
            r = v.run(ACCEPTED_SHOW, "show")
        self.assertEqual(r.outcome, "auto_confirmed")
        self.assertEqual(v.timeouts, [5])          # the action's own deadline is used
        self.assertEqual(v.recordings, 0)          # nothing is recorded

    def test_enter_in_time_records_the_answer(self):
        for transcript, outcome in [("Yes.", "confirmed"), ("No.", "rejected"),
                                    ("O banana", "unclear"), ("", "unclear")]:
            with self.subTest(transcript=transcript):
                v = FakeVoice(enter="", transcript=transcript)
                with policy(5), mock.patch("builtins.print"):
                    r = v.run(ACCEPTED_SHOW, "show")
                self.assertEqual(r.outcome, outcome)
                self.assertEqual(v.recordings, 1)


class ExplicitPolicy(unittest.TestCase):
    def test_waits_without_limit_and_never_auto_confirms(self):
        v = FakeVoice(enter="", transcript="yes")
        with policy(None), mock.patch("builtins.print"):
            r = v.run(ACCEPTED_DIAGNOSIS, "diagnosis")
        self.assertEqual(v.timeouts, [None])       # no deadline for an explicit-reply action
        self.assertEqual(r.outcome, "confirmed")


class ImmediatePolicy(unittest.TestCase):
    def test_no_question_and_no_recording(self):
        v = FakeVoice(enter="")
        with policy(0), mock.patch("builtins.print"):
            r = v.run({"status": "accepted", "action": "emergency", "slots": {}}, "emergency")
        self.assertEqual(r.outcome, "auto_confirmed")
        self.assertEqual((v.timeouts, v.recordings), ([], 0))


class ClarificationIsNotAQuestion(unittest.TestCase):
    def test_no_enter_wait_for_a_clarification(self):
        v = FakeVoice(enter=None)
        with policy(5):
            r = v.run({"status": "needs_clarification", "action": "diagnosis",
                       "clarification_message": "Please say the corrected command."}, "diagnosis")
        self.assertEqual(r.outcome, "clarification_requested")
        self.assertEqual((v.timeouts, v.recordings), ([], 0))


class RealReaderDeadline(unittest.TestCase):
    """With the real TerminalLineReader: no key before the deadline -> None (auto-confirm)."""
    def test_reader_deadline(self):
        now = [0.0]

        def read_char(timeout):          # nothing is typed; time moves on by the timeout
            now[0] += timeout if timeout is not None else 1.0
            return None
        reader = rp.TerminalLineReader("> ", read_char=read_char, write=lambda t: None,
                                       clock=lambda: now[0])
        recorded = []
        answer = rp.voice_answer(reader.read_answer, lambda: recorded.append(1) or "yes")
        with mock.patch("builtins.print"):
            self.assertIsNone(answer(3))
        self.assertEqual(recorded, [])
        self.assertGreaterEqual(now[0], 3.0)

    def test_reader_enter_then_record(self):
        keys = iter(["\n"])
        reader = rp.TerminalLineReader("> ", read_char=lambda t: next(keys, None),
                                       write=lambda t: None, clock=lambda: 0.0)
        # input already waiting when the question appears is not an answer: send Enter after that
        calls = {"n": 0}

        def read_char(timeout):
            calls["n"] += 1
            if timeout == 0 and calls["n"] == 1:
                return None                   # nothing waiting at question time
            return next(keys, None)
        reader._read_char = read_char
        answer = rp.voice_answer(reader.read_answer, lambda: "correct")
        with mock.patch("builtins.print"):
            self.assertEqual(answer(5), "correct")


if __name__ == "__main__":
    unittest.main()
