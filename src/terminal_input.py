#!/usr/bin/env python3
"""Keyboard input for the --interactive and --voice sessions (run_pipeline.py).

TerminalLineReader reads a line key by key, with a deadline, so the session
can show the dictation reminder while the user is idle and can tell typing
apart from pasted text. _cbreak_terminal switches the terminal into the mode
that delivers single keys. Without a terminal (piped input) both fall back to
plain line input without timers.
"""
import codecs
import re
import sys
import time
from typing import Optional

from stage7_confirmation import _wait_for_response

_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")          # other escape sequences (arrow keys, ...)


class TerminalLineReader:
    """Reads a line key by key, so that typing is visible before Enter.

    read_char(timeout) returns what was typed (bytes from the terminal, or str in
    tests), or None if nothing was typed before the timeout; on_activity() is
    called for every input. Bytes are decoded with an incremental UTF-8 decoder
    kept on the reader, so a character split between two reads ("ü" in
    "Müller") is not lost. Characters after a newline in the same input (a pasted
    block of several lines) stay in the queue and become the next lines; nothing
    is dropped. The text typed so far is kept across read() calls, so a reminder
    or a discard message never loses it. Without a terminal (piped input) it falls
    back to input(), without timers.

    Pasted text: bracketed_paste() switches the terminal to bracketed paste mode
    (xterm, macOS Terminal, iTerm2, ...), which marks every paste with
    ESC[200~ ... ESC[201~. Pasted characters are kept and used as normal input,
    but never as the answer to a confirmation question (read_answer). A terminal
    without this mode sends a paste unmarked; it then cannot be told apart from
    typing, and a pasted line that arrives after the question counts as typed."""

    def __init__(self, prompt: str, on_activity=lambda: None, read_char=None, write=None,
                 clock=time.monotonic):
        self.prompt = prompt
        self.on_activity = on_activity
        self._read_char = read_char
        self._write = write or (lambda t: (sys.stdout.write(t), sys.stdout.flush()))
        self._clock = clock
        self.buffer: list[str] = []
        self.queue: list[tuple[str, bool]] = []   # (character, pasted) not consumed yet
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._esc = ""                            # an escape sequence split between reads
        self._in_paste = False
        self._answering = False
        self._shown = False

    @property
    def pending_text(self) -> str:
        return "".join(ch for ch, _ in self.queue)

    def bracketed_paste(self, on: bool):
        """Switch bracketed paste mode of the terminal on or off (no effect without a terminal)."""
        if self._read_char is None and sys.stdin.isatty():
            self._write("\x1b[?2004h" if on else "\x1b[?2004l")

    def redraw(self):
        self._write(self.prompt + "".join(self.buffer))
        self._shown = True

    def _with_terminal(self, fn):
        if self._read_char is not None:
            return fn(self._read_char)
        with _cbreak_terminal() as read_char:
            return fn(read_char)

    def read(self, timeout_fn):
        if self._read_char is None and not sys.stdin.isatty():
            return input(self.prompt)
        return self._with_terminal(lambda rc: self._read(timeout_fn, rc))

    def read_answer(self, timeout: Optional[float], prompt: str = "  answer> ") -> Optional[str]:
        """Answer to a confirmation question (Stage 7 read_response). Only keys typed
        after the question count: input that is already waiting when the question is
        shown, and pasted text (bracketed paste), goes to the queue and is handled
        later as normal input, so it can never answer the question. One deadline for
        the whole question (timeout None: wait without limit); keys do not extend it.
        None if the deadline passes (a partly typed answer is then dropped)."""
        if self._read_char is None and not sys.stdin.isatty():
            return _stdin_answer(timeout)
        deadline = None if timeout is None else self._clock() + timeout

        def remaining():
            return None if deadline is None else max(0.0, deadline - self._clock())

        def answer(rc):
            while True:                                  # input already waiting: not an answer
                chunk = rc(0)
                if chunk is None:
                    break
                self._ingest(chunk)
            saved = (self.queue, self.buffer, self._shown, self.prompt)
            self.queue, self.buffer, self._shown, self.prompt = [], [], False, prompt
            self._answering, self._held = True, []
            try:
                return self._read(remaining, rc)
            finally:
                rest, held = self.queue, self._held      # held: pasted while answering
                self.queue, self.buffer, self._shown, self.prompt = saved
                self.queue = self.queue + held + rest
                self._answering, self._shown = False, False
        return self._with_terminal(answer)

    def _ingest(self, chunk):
        """Decode a chunk and add it to the queue, marking pasted characters."""
        text = self._esc + (self._decoder.decode(bytes(chunk))
                            if isinstance(chunk, (bytes, bytearray)) else chunk)
        self._esc = ""
        i = 0
        while i < len(text):
            if text[i] == "\x1b":
                rest = text[i:]
                if rest.startswith(_PASTE_START):
                    self._in_paste, i = True, i + len(_PASTE_START)
                    continue
                if rest.startswith(_PASTE_END):
                    self._in_paste, i = False, i + len(_PASTE_END)
                    continue
                m = _CSI.match(rest)
                if m:
                    i += m.end()                         # ignore other escape sequences
                    continue
                if len(rest) < 8 and not re.search(r"[@-~]", rest[2:]):
                    self._esc = rest                     # incomplete: wait for the rest
                    break
                i += 1                                   # a lone ESC
                continue
            self.queue.append((text[i], self._in_paste))
            i += 1

    def _read(self, timeout_fn, read_char):
        if not self._shown:
            self.redraw()
        while True:
            if not self.queue:
                chunk = read_char(timeout_fn())
                if chunk is None:
                    return None                        # deadline passed without input
                self._ingest(chunk)
                self.on_activity()
            line = self._consume()
            if line is not None:
                return line

    def _consume(self) -> Optional[str]:
        """Apply queued characters to the line; return the line at a newline (the rest
        stays queued), else None. While answering a question, pasted characters are
        held back for later instead."""
        while self.queue:
            (ch, pasted), self.queue = self.queue[0], self.queue[1:]
            if self._answering and pasted:
                self._held.append((ch, pasted))
                continue
            if ch in ("\r", "\n"):
                if ch == "\r" and self.queue and self.queue[0][0] == "\n":
                    self.queue = self.queue[1:]          # \r\n is one line end
                line, self.buffer, self._shown = "".join(self.buffer), [], False
                self._write("\n")
                return line
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04" and not self.buffer:
                raise EOFError
            if ch in ("\x7f", "\b"):
                if self.buffer:
                    self.buffer.pop()
                    self._write("\b \b")
            elif ch.isprintable():
                self.buffer.append(ch)
                self._write(ch)
        return None


def _stdin_answer(timeout):
    """Piped input: one line, with the same timeout rule as Stage 7 uses without a reader."""
    return _wait_for_response(timeout)


class _cbreak_terminal:
    """Context manager: terminal in cbreak mode (keys arrive one by one, no echo),
    restored afterwards. Yields read_char(timeout)."""

    def __enter__(self):
        import termios
        import tty
        self._fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        # TCSANOW: the default (TCSAFLUSH) would throw away input that is already
        # waiting (type-ahead, the rest of a paste) every time a read starts
        tty.setcbreak(self._fd, termios.TCSANOW)
        return self._read_char

    def _read_char(self, timeout):
        import os
        import select as _select
        ready, _, _ = _select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self._fd, 1024)          # bytes: decoded incrementally by the reader

    def __exit__(self, *exc):
        import termios
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        return False
