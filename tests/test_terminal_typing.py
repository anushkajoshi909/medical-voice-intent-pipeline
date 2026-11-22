"""Second review: typing is activity, so an open note is never discarded while the
user is typing a continuation (a typed line only arrives on Enter). Real silence
still discards it. Keystrokes are simulated with a fake clock: no real terminal,
no real waiting."""
import contextlib
import io
import unittest

from helpers import FakeClient, FakeClock, answer
from test_interaction import Harness, NOTE_OPEN, M, SETTINGS
import run_pipeline as rp
import stage7_confirmation as s7
from interaction import InteractionSession


class FakeKeyboard:
    """read_char(timeout) for TerminalLineReader. events: (seconds after the previous
    event, characters). Moves the fake clock; returns None if the timeout passes first."""

    def __init__(self, clock, events):
        self.clock, self.events = clock, list(events)

    def __call__(self, timeout):
        if not self.events:
            if timeout is None:
                raise EOFError                       # nothing more to type and nothing scheduled
            self.clock.advance(timeout)
            return None
        delay, chars = self.events[0]
        if timeout is not None and delay > timeout:
            self.clock.advance(timeout)
            self.events[0] = (delay - timeout, chars)
            return None
        self.clock.advance(delay)
        self.events.pop(0)
        return chars


def typing(text, every):
    return [(every, ch) for ch in text]


def wait(h, keyboard):
    reader = rp.TerminalLineReader("> ", on_activity=h.session.note_activity,
                                   read_char=keyboard, write=lambda t: None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        line = rp._wait_with_reminder(h.session, reader)
    return line, out.getvalue()


class TypingIsActivity(unittest.TestCase):
    def open_note(self):
        h = Harness(NOTE_OPEN)
        h.say("ok glasses set note patient presenting with fever")
        return h

    def test_continuous_typing_across_both_deadlines(self):
        h = self.open_note()
        text = "and mild cough since yesterday evening, pain score 3 of 10"
        start = h.clock()
        line, shown = wait(h, FakeKeyboard(h.clock, typing(text, 3) + [(3, "\n")]))
        self.assertGreater(h.clock() - start, 65)            # longer than reminder + discard
        self.assertEqual(line, text)
        self.assertNotIn("discarded", shown)
        self.assertNotIn("end phrase", shown)                # no pause of 5 s, so no question
        self.assertEqual(h.session.state, "pending_dictation")
        t = h.say(line)
        self.assertEqual(t.kind, "dictation_continued")
        self.assertEqual(h.session.pending.content, f"patient presenting with fever {text}")

    def test_typing_after_the_question_keeps_note_and_buffer(self):
        h = self.open_note()
        events = (typing("temp 38", 1) + [(6, " ")]           # 6 s pause: the question appears
                  + typing("since morning, no chills, no rash, drinking well today", 3)  # about 160 s
                  + [(3, "\n")])
        line, shown = wait(h, FakeKeyboard(h.clock, events))
        self.assertIn("end phrase", shown)                   # the question was shown once
        self.assertNotIn("discarded", shown)
        self.assertEqual(line, "temp 38 since morning, no chills, no rash, drinking well today")
        self.assertEqual(h.say(line).kind, "dictation_continued")
        self.assertTrue(h.session.pending.content.endswith("drinking well today"))

    def test_backspace_edits_the_buffer(self):
        h = self.open_note()
        line, _ = wait(h, FakeKeyboard(h.clock, typing("feverr", 1) + [(1, "\x7f"), (1, "\n")]))
        self.assertEqual(line, "fever")

    def test_real_silence_still_discards(self):
        h = self.open_note()
        with self.assertRaises(EOFError):                    # the fake keyboard has nothing to type
            wait(h, FakeKeyboard(h.clock, []))
        self.assertEqual(h.session.state, "idle")
        self.assertEqual(h.asked, [])

    def test_silence_after_partial_typing_discards_but_keeps_the_typed_text(self):
        """A pause longer than reminder + discard is real inactivity: the open note is
        discarded, but what is in the input line is not deleted; sending it later is a
        new input (without the trigger: ignored)."""
        h = self.open_note()
        keyboard = FakeKeyboard(h.clock, typing("half a sen", 1) + [(70, "tence\n")])
        line, shown = wait(h, keyboard)
        self.assertIn("discarded", shown)
        self.assertEqual(line, "half a sentence")
        self.assertEqual(h.say(line).kind, "inactive")



def reader_for(keyboard, on_activity=lambda: None):
    return rp.TerminalLineReader("> ", on_activity=on_activity, read_char=keyboard,
                                 write=lambda t: None, clock=keyboard.clock)


class PastedLines(unittest.TestCase):
    """Third review, bug 1: characters after a newline in the same input are kept."""

    def test_several_lines_in_one_input(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, "first continuation\nsecond continuation\r\nthird")]))
        self.assertEqual(r.read(lambda: None), "first continuation")
        self.assertEqual(r.read(lambda: None), "second continuation")   # was lost before
        self.assertEqual(r.read(lambda: 5), None)                         # 'third' has no newline yet
        self.assertEqual("".join(r.buffer), "third")                      # ... but is kept

    def session_with_terminal(self, keyboard_events, *answers):
        clock = FakeClock()
        client = FakeClient(*answers)
        keyboard = FakeKeyboard(clock, keyboard_events)
        reader = reader_for(keyboard)
        session = InteractionSession(
            process=lambda text: rp.run_command(client, {"id": "s", "text": text, "valid": None,
                                                         "expected_action": None, "category": "t",
                                                         "action_source": "t"}, M),
            complete=rp.complete_dictation,
            confirm=lambda payload, action: s7.run(payload, action, read_response=reader.read_answer),
            settings=SETTINGS, clock=clock)
        reader.on_activity = session.note_activity
        return session, reader, client

    def run_turns(self, session, reader, n):
        turns = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(n):
                turns.append(session.handle(rp._wait_with_reminder(session, reader)))
        return turns

    def test_pasted_yes_cannot_answer_the_confirmation(self):
        # a pasted block: a command and a 'yes' line; the user then types 'no' as the answer
        session, reader, client = self.session_with_terminal(
            [(1, "ok glasses diagnosis pneumonia\nyes\n"), (2, "no\n")],
            answer("diagnosis", "pneumonia"))
        first, second = self.run_turns(session, reader, 2)
        self.assertEqual(first.stage7.outcome, "rejected")             # the typed answer, not the pasted 'yes'
        self.assertEqual(second.result["text"], "yes")                 # the pasted line is kept ...
        self.assertEqual(second.kind, "inactive")                      # ... and is a new input, not a confirmation
        self.assertEqual(len(client.calls), 1)

    def test_input_waiting_when_the_question_appears_is_not_an_answer(self):
        # 'yes' was typed ahead while the command was being processed
        session, reader, _ = self.session_with_terminal(
            [(1, "ok glasses diagnosis pneumonia\n"), (0, "yes\n"), (2, "wrong\n")],
            answer("diagnosis", "pneumonia"))
        first, second = self.run_turns(session, reader, 2)
        self.assertEqual(first.stage7.outcome, "rejected")
        self.assertEqual(second.result["text"], "yes")

    def test_marked_paste_after_a_gap_is_not_an_answer(self):
        """Fourth review, bug 1: a paste arriving 100 ms (or 3 s) after the question
        must not answer it. Bracketed paste marks it, whatever the timing."""
        for gap in (0.1, 3):
            with self.subTest(gap=gap):
                session, reader, _ = self.session_with_terminal(
                    [(1, "ok glasses diagnosis pneumonia\n"), (gap, "\x1b[200~yes\n\x1b[201~"), (2, "no\n")],
                    answer("diagnosis", "pneumonia"))
                first, second = self.run_turns(session, reader, 2)
                self.assertEqual(first.stage7.outcome, "rejected")     # the typed answer
                self.assertEqual(second.result["text"], "yes")         # the paste is kept, as a new input
                self.assertEqual(second.kind, "inactive")

    def test_paste_markers_split_between_reads(self):
        session, reader, _ = self.session_with_terminal(
            [(1, "ok glasses diagnosis pneumonia\n"), (0.2, "\x1b[20"), (0.01, "0~yes\n\x1b[2"),
             (0.01, "01~"), (2, "correct\n")],
            answer("diagnosis", "pneumonia"))
        first, second = self.run_turns(session, reader, 2)
        self.assertEqual(first.stage7.outcome, "confirmed")             # by the typed 'correct'
        self.assertEqual(second.result["text"], "yes")

    def test_pasted_text_is_normal_input_outside_questions(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, "\x1b[200~first line\nsecond\x1b[201~ line\n")]))
        self.assertEqual(r.read(lambda: None), "first line")
        self.assertEqual(r.read(lambda: None), "second line")

    def test_arrow_keys_are_not_text(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, "fev\x1b[Der\x1b"), (1, "[A\n")]))
        self.assertEqual(r.read(lambda: None), "fever")

    def test_typed_answer_after_the_question_confirms(self):
        session, reader, _ = self.session_with_terminal(
            [(1, "ok glasses diagnosis pneumonia\n"), (2, "correct\n")], answer("diagnosis", "pneumonia"))
        (first,) = self.run_turns(session, reader, 1)
        self.assertEqual(first.stage7.outcome, "confirmed")

    def test_pasted_dictation_lines_are_all_kept(self):
        session, reader, _ = self.session_with_terminal(
            [(1, "ok glasses set note patient presenting with fever\nsince yesterday\nno allergies\n")],
            NOTE_OPEN)
        self.run_turns(session, reader, 3)
        self.assertEqual(session.pending.content, "patient presenting with fever since yesterday no allergies")


class AnswerDeadline(unittest.TestCase):
    """Fourth review, bug 2: one deadline per confirmation question."""

    def test_keys_do_not_extend_the_deadline(self):
        clock = FakeClock()
        kb = FakeKeyboard(clock, [(4, "y"), (4, "e"), (4, "s"), (4, "\n")])
        r = reader_for(kb)
        start = clock()
        self.assertIsNone(r.read_answer(5))                             # 'yes' would end after 16 s
        self.assertAlmostEqual(clock() - start, 5)

    def test_waiting_input_time_counts(self):
        clock = FakeClock()
        kb = FakeKeyboard(clock, [(0, "typed ahead\n"), (4.5, "n"), (1, "o\n")])
        r = reader_for(kb)
        start = clock()
        self.assertIsNone(r.read_answer(5))
        self.assertLessEqual(clock() - start, 5 + 1e-9)
        self.assertEqual(r.read(lambda: None), "typed ahead")           # kept for later

    def test_answer_in_time(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, "y"), (1, "e"), (1, "s"), (1, "\n")]))
        self.assertEqual(r.read_answer(5), "yes")

    def test_no_timeout_waits_without_limit(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(40, "y"), (40, "e"), (40, "s"), (40, "\n")]))
        self.assertEqual(r.read_answer(None), "yes")
        self.assertAlmostEqual(clock(), 1000 + 160)

    def test_timed_policy_through_stage7(self):
        """show has a 5 s policy: a slow 'no' (4 s per key) is too late, so the policy applies."""
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(4, "n"), (4, "o"), (4, "\n")]))
        payload = {"status": "accepted", "action": "show", "slots": {}}
        with contextlib.redirect_stdout(io.StringIO()):
            out = s7.run(payload, "show", read_response=r.read_answer)
        self.assertEqual(out.outcome, "auto_confirmed")
        self.assertAlmostEqual(clock() - 1000, 5)


class SplitUtf8(unittest.TestCase):
    """Third review, bug 2: a character split between two reads is not lost."""

    def test_split_between_reads(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, b"change patient to M\xc3"), (1, b"\xbcller\n")]))
        self.assertEqual(r.read(lambda: None), "change patient to Müller")

    def test_split_across_a_timeout(self):
        clock = FakeClock()
        r = reader_for(FakeKeyboard(clock, [(1, b"Jos\xc3"), (10, b"\xa9 \xe2\x82"), (1, b"\xac\n")]))
        self.assertIsNone(r.read(lambda: 5))                            # deadline passes mid-character
        self.assertEqual(r.read(lambda: None), "José €")

    def test_every_byte_separately(self):
        clock = FakeClock()
        text = "Müller, Ærø, 38.5 °C, Straße"
        r = reader_for(FakeKeyboard(clock, [(0.1, bytes([b])) for b in (text + "\n").encode()]))
        self.assertEqual(r.read(lambda: None), text)



class TerminalModeKeepsWaitingInput(unittest.TestCase):
    """Found while checking the third review in a pseudo-terminal: tty.setcbreak()
    defaults to TCSAFLUSH, which discards input already waiting (type-ahead, the rest
    of a paste) whenever a read starts. The reader must switch with TCSANOW."""

    def test_switch_does_not_flush(self):
        import termios
        from unittest import mock
        with mock.patch("termios.tcgetattr", return_value=["old"]), \
             mock.patch("termios.tcsetattr") as restore, \
             mock.patch("tty.setcbreak") as setcbreak, \
             mock.patch.object(rp.sys.stdin, "fileno", return_value=0):
            import terminal_input
            with terminal_input._cbreak_terminal():
                pass
        setcbreak.assert_called_once_with(0, termios.TCSANOW)
        restore.assert_called_once_with(0, termios.TCSADRAIN, ["old"])   # restoring does not flush either


if __name__ == "__main__":
    unittest.main()
