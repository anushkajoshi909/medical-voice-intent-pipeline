"""Finding 9: strict schema for the Stage 2 answer."""
import json
import math
import unittest

from helpers import FakeClient, answer
from stage2_llm_extract import extract, parse_extraction

M = "fake-model"


def parse(d):
    return parse_extraction(d if isinstance(d, str) else json.dumps(d), M)


class ValidAnswers(unittest.TestCase):
    def test_valid_answer(self):
        r = parse(answer("show", "blood pressure", confidence=0.97))
        self.assertIsNone(r.error)
        self.assertEqual((r.action, r.entity, r.end_signal, r.confidence), ("show", "blood pressure", False, 0.97))

    def test_fenced_json(self):
        r = parse("```json\n" + json.dumps(answer("diagnosis", "pneumonia")) + "\n```")
        self.assertIsNone(r.error)
        self.assertEqual(r.entity, "pneumonia")

    def test_empty_entity_is_valid(self):
        r = parse(answer("emergency", ""))
        self.assertIsNone(r.error)
        self.assertEqual(r.entity, "")

    def test_unknown_action_is_allowed(self):
        r = parse(answer("UNKNOWN", "", confidence=0.1))
        self.assertIsNone(r.error)
        self.assertEqual(r.action, "UNKNOWN")

    def test_integer_confidence_bounds(self):
        self.assertIsNone(parse(answer("show", "x", confidence=1)).error)
        self.assertIsNone(parse(answer("show", "x", confidence=0)).error)

    def test_recipient_rules(self):
        ok = parse(answer("send_message", "patient is stable", recipient="Dr Smith"))
        self.assertIsNone(ok.error)
        self.assertEqual(ok.recipient, "Dr Smith")
        other = parse(answer("show", "x"))           # omitted for another action -> ""
        self.assertIsNone(other.error)
        self.assertEqual(other.recipient, "")


class MalformedAnswers(unittest.TestCase):
    def assertSchemaFailure(self, raw):
        r = parse(raw)
        self.assertEqual(r.error_kind, "schema", r.error)
        self.assertEqual(r.action, "UNKNOWN")
        self.assertFalse(r.valid_action)                  # Stage 6 rejects it as unrecognised
        self.assertTrue(r.error.startswith("schema error"))
        self.assertEqual(r.raw_response, raw if isinstance(raw, str) else json.dumps(raw))
        return r

    def test_missing_fields(self):
        for key in ("action", "entity", "end_signal", "confidence"):
            d = answer("show", "x")
            del d[key]
            with self.subTest(missing=key):
                r = self.assertSchemaFailure(d)
                self.assertIn(key, r.error)

    def test_nulls(self):
        for key in ("action", "entity", "end_signal", "confidence"):
            d = answer("show", "x")
            d[key] = None
            with self.subTest(null=key):
                self.assertSchemaFailure(d)

    def test_wrong_scalar_and_container_types(self):
        for key, value in [("entity", 5), ("entity", ["a"]), ("entity", {"a": 1}),
                           ("action", 3), ("confidence", "0.9"), ("confidence", True),
                           ("end_signal", 1), ("end_signal", "false"), ("end_signal", "true")]:
            d = answer("show", "x")
            d[key] = value
            with self.subTest(key=key, value=value):
                self.assertSchemaFailure(d)

    def test_end_signal_string_false_is_not_true(self):
        r = parse(answer("set_note", "x", end_signal="false"))
        self.assertEqual(r.error_kind, "schema")
        self.assertFalse(r.end_signal)

    def test_unknown_action_name(self):
        self.assertSchemaFailure(answer("make_coffee", "x"))

    def test_confidence_range_and_finite(self):
        for c in (-0.1, 1.01, 2):
            with self.subTest(confidence=c):
                self.assertSchemaFailure(answer("show", "x", confidence=c))
        # NaN and Infinity are accepted by json.loads, so send them as JSON tokens
        for token in ("NaN", "Infinity", "-Infinity"):
            raw = '{"action":"show","entity":"x","end_signal":false,"confidence":%s}' % token
            with self.subTest(confidence=token):
                self.assertSchemaFailure(raw)
        self.assertTrue(math.isnan(json.loads('{"c": NaN}')["c"]))   # sanity: the token reaches the schema

    def test_extra_fields(self):
        self.assertSchemaFailure(answer("show", "x", reasoning="because"))

    def test_send_message_needs_recipient(self):
        self.assertSchemaFailure(answer("send_message", "patient is stable"))
        self.assertSchemaFailure(answer("send_message", "patient is stable", recipient=None))

    def test_not_an_object(self):
        r = self.assertSchemaFailure('["show", "x"]')
        self.assertIn("not an object", r.error)


class ErrorKinds(unittest.TestCase):
    def test_syntax_error(self):
        r = parse("this is not json")
        self.assertEqual((r.action, r.error_kind), ("UNKNOWN", "syntax"))
        self.assertEqual(r.raw_response, "this is not json")

    def test_api_failure_and_null_content(self):
        for a in (RuntimeError("connection refused"), None):
            with self.subTest(answer=a):
                r = extract(FakeClient(a), "show bp", model=M)
                self.assertEqual((r.action, r.error_kind), ("UNKNOWN", "api"))

    def test_extract_uses_schema(self):
        r = extract(FakeClient(answer("set_note", "x", end_signal="false")), "set note x", model=M)
        self.assertEqual(r.error_kind, "schema")
        good = extract(FakeClient(answer("show", "heart rate")), "show heart rate", model=M)
        self.assertIsNone(good.error)
        self.assertEqual(good.entity, "heart rate")


if __name__ == "__main__":
    unittest.main()
