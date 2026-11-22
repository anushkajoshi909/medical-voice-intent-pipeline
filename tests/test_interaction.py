"""Open dictation across turns, and the session side of clarification and the trigger gate.

Fake model answers, scripted replies, and a fake clock: no remote calls, no
microphone, no real waiting."""
import unittest

from helpers import FakeClient, FakeClock, answer
import run_pipeline as rp
import stage7_confirmation as s7
from domain_loader import load
from interaction import InteractionSession

M = "fake-model"
SETTINGS = load()["interaction"]


class Harness:
    """A session wired to the real pipeline with a fake model client."""

    def __init__(self, *answers, replies=(), settings=SETTINGS):
        self.client = FakeClient(*answers)
        self.clock = FakeClock()
        self.replies = list(replies)          # scripted Stage 7 replies (None = silence)
        self.asked = []
        self.session = InteractionSession(
            process=lambda text: rp.run_command(self.client, {
                "id": "s", "text": text, "valid": None, "expected_action": None,
                "category": "test", "action_source": "test"}, M),
            complete=rp.complete_dictation,
            confirm=lambda payload, action: s7.run(payload, action, respond=self._respond),
            settings=settings, clock=self.clock)

    def _respond(self, prompt):
        self.asked.append(prompt)
        return self.replies.pop(0) if self.replies else None

    def say(self, text):
        return self.session.handle(text)


NOTE_OPEN = answer("set_note", "patient presenting with fever", end_signal=False)
MSG_OPEN = answer("send_message", "please check the lab results", end_signal=False, recipient="Dr Smith")


class OpenDictation(unittest.TestCase):
    def test_note_without_end_signal_opens_dictation(self):
        h = Harness(NOTE_OPEN)
        t = h.say("ok glasses set note patient presenting with fever")
        self.assertEqual(t.kind, "dictation_open")
        self.assertEqual(h.session.state, "pending_dictation")
        self.assertEqual(h.session.pending.content, "patient presenting with fever")
        self.assertEqual(h.asked, [])                         # nothing to confirm yet

    def test_note_with_end_signal_goes_straight_to_confirmation(self):
        h = Harness(answer("set_note", "patient is stable", end_signal=True), replies=["yes"])
        t = h.say("ok glasses set note patient is stable finish note")
        self.assertEqual(t.kind, "confirmation")
        self.assertEqual(t.stage7.outcome, "confirmed")
        self.assertEqual(h.session.state, "idle")

    def test_multiple_continuation_turns_without_trigger(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.say("since yesterday evening")
        t = h.say("ok glasses no known allergies")             # a trigger inside the dictation is removed
        self.assertEqual(t.kind, "dictation_continued")
        self.assertEqual(h.session.pending.content,
                         "patient presenting with fever since yesterday evening no known allergies")
        self.assertEqual(len(h.client.calls), 1)             # follow-ups make no model call

    def test_message_keeps_recipient_and_content(self):
        h = Harness(MSG_OPEN, replies=["correct"])
        h.say("ok glasses send a message to Dr Smith please check the lab results")
        h.say("before the ward round")
        self.assertEqual(h.session.pending.recipient, "Dr Smith")
        t = h.say("finish message")
        self.assertEqual(t.kind, "dictation_finished")
        slot = t.result["s6_payload"]["slots"]["free_text"]
        self.assertEqual(slot["content"], "please check the lab results before the ward round")
        self.assertEqual(slot["recipient"], "Dr Smith")
        self.assertEqual(t.stage7.outcome, "confirmed")
        self.assertEqual(len(h.client.calls), 1)


class FinishContinueCancel(unittest.TestCase):
    def test_finish_validates_then_applies_normal_confirmation(self):
        h = Harness(NOTE_OPEN, replies=["no"])
        h.say("ok glasses set note patient presenting with fever")
        t = h.say("finish")
        self.assertEqual(t.result["s6_status"], "accepted")       # Stages 3-6 ran on the full content
        self.assertEqual(len(h.asked), 1)                         # the confirmation question was asked
        self.assertEqual(t.stage7.outcome, "rejected")            # finishing is not a confirmation
        self.assertEqual(h.session.state, "idle")

    def test_punctuated_finish_phrase_keeps_the_content(self):
        """Review bug 3: only the span of the finish phrase is removed."""
        for said, kept in [("oxygen saturation is 98%. Finish-the-note.", "oxygen saturation is 98%."),
                           ("blood pressure 120/80, finish note", "blood pressure 120/80"),
                           ("dose 2.5 mg; Finish the note!", "dose 2.5 mg"),
                           ("ok glasses, pain score 3 of 10 - end of note", "pain score 3 of 10")]:
            with self.subTest(said=said):
                h = Harness(NOTE_OPEN, replies=["yes"])
                h.say("ok glasses set note patient presenting with fever")
                t = h.say(said)
                self.assertEqual(t.kind, "dictation_finished")
                self.assertEqual(t.result["s6_payload"]["slots"]["free_text"]["content"],
                                 f"patient presenting with fever {kept}")

    def test_finish_word_inside_content_is_content(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        t = h.say("finish note taking after the ward round")       # phrase not at the end
        self.assertEqual(t.kind, "dictation_continued")

    def test_end_phrase_inside_continuation(self):
        h = Harness(NOTE_OPEN, replies=["yes"])
        h.say("ok glasses set note patient presenting with fever")
        t = h.say("the wound is clean, finish note")
        self.assertEqual(t.kind, "dictation_finished")
        self.assertEqual(t.result["s6_payload"]["slots"]["free_text"]["content"],
                         "patient presenting with fever the wound is clean")

    def test_continue_after_reminder(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(6)
        self.assertIsNotNone(h.session.reminder())
        self.assertEqual(h.say("continue").kind, "dictation_resumed")
        self.assertEqual(h.say("and mild cough").kind, "dictation_continued")
        self.assertEqual(h.session.pending.content, "patient presenting with fever and mild cough")

    def test_cancel_clears_state(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        self.assertEqual(h.say("cancel").kind, "dictation_cancelled")
        self.assertIsNone(h.session.pending)
        self.assertEqual(h.asked, [])
        self.assertEqual(h.say("show heart rate").kind, "inactive")   # back to idle: trigger needed again

    def test_continuation_sent_after_the_reminder_is_kept(self):
        """Review bug 1: text typed (or spoken) before the question appeared and sent
        after it is dictated content, not an unclear answer to the question."""
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(5)
        self.assertIsNotNone(h.session.reminder())
        t = h.say("temperature 38.5 since this morning")
        self.assertEqual(t.kind, "dictation_continued")
        self.assertEqual(h.session.pending.content,
                         "patient presenting with fever temperature 38.5 since this morning")
        self.assertIsNone(h.session.expire())                # answered: no discard
        self.assertEqual(h.asked, [])

    def test_empty_reply_is_unclear_and_changes_nothing(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        t = h.say("  ...  ")
        self.assertEqual(t.kind, "unclear_reply")
        self.assertEqual(h.session.pending.content, "patient presenting with fever")
        self.assertEqual(h.asked, [])

    def test_silence_discards_and_never_sends(self):
        h = Harness(MSG_OPEN)
        h.say("ok glasses send a message to Dr Smith please check the lab results")
        h.clock.advance(5)
        self.assertIsNotNone(h.session.reminder())
        self.assertAlmostEqual(h.session.seconds_until_next_event(), 60)
        h.clock.advance(59.9)
        self.assertIsNone(h.session.expire())                  # not yet
        self.assertEqual(h.session.state, "pending_dictation")
        h.clock.advance(0.1)
        msg = h.session.expire()
        self.assertEqual(msg, "No answer: the open message was discarded. Nothing was saved or sent.")
        self.assertEqual(h.session.state, "idle")
        self.assertEqual(h.asked, [])                          # never confirmed or sent
        self.assertIsNone(h.session.expire())
        self.assertEqual(h.say("finish").kind, "inactive")     # nothing left to finish

    def test_no_discard_without_the_question(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.session.begin_input()                                # still recording: no question, no discard
        h.clock.advance(300)
        self.assertIsNone(h.session.reminder())
        self.assertIsNone(h.session.expire())
        self.assertEqual(h.session.state, "pending_dictation")
        h.session.end_input()

    def test_answer_after_the_question_stops_the_discard(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(5)
        h.session.reminder()
        h.clock.advance(30)
        h.say("continue")
        h.clock.advance(40)                                     # 70 s after the question, but the user answered
        self.assertIsNone(h.session.expire())
        self.assertEqual(h.session.state, "pending_dictation")


class Reminder(unittest.TestCase):
    def test_timing_once_per_wait(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(4.9)
        self.assertIsNone(h.session.reminder())
        h.clock.advance(0.1)
        self.assertEqual(h.session.reminder(),
                         "I did not hear an end phrase for this note. "
                         "Is the note finished, or would you like to continue?")
        h.clock.advance(20)
        self.assertIsNone(h.session.reminder())               # shown once until the user speaks again

    def test_message_wording(self):
        h = Harness(MSG_OPEN)
        h.say("ok glasses send a message to Dr Smith please check the lab results")
        h.clock.advance(5)
        self.assertIn("end phrase for this message", h.session.reminder())

    def test_no_reminder_while_recording(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        h.session.begin_input()
        h.clock.advance(30)
        self.assertFalse(h.session.reminder_due())
        h.session.end_input()

    def test_recording_time_is_excluded_after_recording_ends(self):
        """Review bug 4: busy time must not count once recording has ended, also when the
        transcript is empty and handle() is not called."""
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        # before the reminder: 2 s idle, then a 30 s recording without a usable transcript
        h.clock.advance(2)
        h.session.begin_input()
        h.clock.advance(30)
        h.session.end_input()
        self.assertAlmostEqual(h.session.seconds_until_next_event(), 3)
        self.assertIsNone(h.session.reminder())
        h.clock.advance(3)
        self.assertIsNotNone(h.session.reminder())
        # after the reminder: 1 s, then a 65 s recording without a usable transcript
        h.clock.advance(1)
        h.session.begin_input()
        h.clock.advance(65)
        h.session.end_input()
        self.assertIsNone(h.session.expire())
        self.assertAlmostEqual(h.session.seconds_until_next_event(), 59)
        self.assertEqual(h.session.state, "pending_dictation")
        h.clock.advance(59)
        self.assertIsNotNone(h.session.expire())                 # the real 60 s of silence

    def test_processing_time_is_not_inactivity(self):
        h = Harness()
        slow_process = h.session._process

        def process(text):
            h.clock.advance(4)                                 # model/network time
            return slow_process(text)
        h.session._process = process
        h.client.answers = [NOTE_OPEN]
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(4.9)                                   # measured from the end of processing
        self.assertFalse(h.session.reminder_due())
        h.clock.advance(0.1)
        self.assertTrue(h.session.reminder_due())

    def test_configurable_interval(self):
        h = Harness(NOTE_OPEN, settings=dict(SETTINGS, dictation_reminder_sec=2))
        h.say("ok glasses set note patient presenting with fever")
        h.clock.advance(2)
        self.assertTrue(h.session.reminder_due())

    def test_no_reminder_when_idle(self):
        h = Harness()
        h.clock.advance(100)
        self.assertIsNone(h.session.reminder())


class SessionWithFindings7And8(unittest.TestCase):
    def test_new_command_needs_trigger(self):
        h = Harness(answer("show", "heart rate"))
        self.assertEqual(h.say("show heart rate").kind, "inactive")
        self.assertEqual(h.client.calls, [])

    def test_clarification_returns_to_listening(self):
        h = Harness(answer("diagnosis", "hemodialysis"),
                    answer("diagnosis", "pneumonia"), replies=["yes"])
        t = h.say("ok glasses diagnosis hemodialysis")
        self.assertEqual(t.kind, "clarification")
        self.assertEqual(t.stage7.outcome, "clarification_requested")
        self.assertEqual(h.asked, [])                          # no yes/no question
        self.assertEqual(h.session.state, "idle")
        self.assertEqual(h.say("yes").kind, "inactive")        # a bare 'yes' is not a confirmation
        t2 = h.say("ok glasses diagnosis pneumonia")           # the corrected command is a new command
        self.assertEqual(t2.result["s6_status"], "accepted")
        self.assertEqual(t2.stage7.outcome, "confirmed")
        self.assertEqual(len(h.client.calls), 2)

    def test_confirmation_reply_needs_no_trigger(self):
        h = Harness(answer("diagnosis", "pneumonia"), replies=["yes"])
        t = h.say("ok glasses diagnosis pneumonia")
        self.assertEqual(t.stage7.outcome, "confirmed")

    def test_sessions_are_isolated(self):
        a, b = Harness(NOTE_OPEN), Harness()
        a.say("ok glasses set note patient presenting with fever")
        self.assertEqual(a.session.state, "pending_dictation")
        self.assertEqual(b.session.state, "idle")


class OneShotText(unittest.TestCase):
    def test_open_dictation_in_text_mode_is_reported_not_confirmed(self):
        r = rp.run_command(FakeClient(NOTE_OPEN), {"id": "t", "text": "ok glasses set note fever",
                                                   "valid": None, "expected_action": None,
                                                   "category": "t", "action_source": "t"}, M)
        self.assertEqual(r["s6_status"], "accepted_partial")
        self.assertEqual(s7.run(r["s6_payload"], r["action"], respond=lambda p: "yes").outcome,
                         "not_applicable")


if __name__ == "__main__":
    unittest.main()
