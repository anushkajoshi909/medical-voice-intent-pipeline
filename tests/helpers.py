"""Shared fakes for the tests: no remote model, no microphone, no real waiting."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "evaluation" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class FakeClient:
    """Stands in for the OpenAI client. Returns the given answers in order (a dict
    is sent as JSON, a str as it is, an Exception is raised) and counts the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        a = self.answers.pop(0) if self.answers else {"action": "UNKNOWN", "entity": "",
                                                       "end_signal": False, "confidence": 0.1}
        if isinstance(a, Exception):
            raise a
        content = a if isinstance(a, str) or a is None else json.dumps(a)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def answer(action, entity="", end_signal=False, confidence=0.9, **extra):
    d = {"action": action, "entity": entity, "end_signal": end_signal, "confidence": confidence}
    d.update(extra)
    return d
