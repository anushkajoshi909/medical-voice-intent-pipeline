#!/usr/bin/env python3
"""Stage 2: LLM Structured Extraction

Input:  clean_text from Stage 1 (trigger stripped, abbreviations expanded)
Output: LLMResult dataclass with action, entity, end_signal, confidence

The answer is checked against a strict schema (ExtractionSchema) before an
LLMResult is built: all four fields are required, types are not coerced
("false" is not a Boolean, null is not a string), confidence must be a finite
number in [0, 1], the action must be a configured action or UNKNOWN, a
send_message answer must carry a recipient, and extra fields are rejected.
A failed check gives action UNKNOWN with error_kind "schema", so a malformed
answer is never treated as a valid extraction. error_kind separates the three
failure kinds: "api" (call failed, or content is null), "syntax" (empty or
non-JSON text), and
"schema" (JSON that breaks the schema).

Model: any chat model of the ScaDS.AI service. The model is taken from
.selected_model (written by the --select picker), otherwise "alias-ha". The
evaluation passes the model explicitly (run_eval.py --models): Llama 3.3 70B,
Gemma 4 26B, gpt-oss-120b and DeepSeek V4.1 Flash (evaluation/evaluation.md).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain_loader import load as _load_domain

ROOT = Path(__file__).resolve().parent.parent

_domain       = _load_domain()
SYSTEM_PROMPT = _domain["system_prompt"]
VALID_ACTIONS = _domain["valid_actions"]
_model_file = ROOT / ".selected_model"
DEFAULT_MODEL = _model_file.read_text().strip() if _model_file.exists() else "alias-ha"


def get_client() -> OpenAI:
    key = (Path.home() / ".scadsai-api-key").read_text().splitlines()[0].strip()
    return OpenAI(base_url="https://llm.scads.ai/v1", api_key=key)


@dataclass
class LLMResult:
    action: str
    entity: str
    end_signal: bool
    confidence: float
    raw_response: str
    model: str
    recipient: str = ""      # send_message only: the person the message is addressed to
    error: str | None = None
    error_kind: str | None = None   # None | "api" | "syntax" | "schema"

    @property
    def valid_action(self) -> bool:
        return self.action in VALID_ACTIONS


class ExtractionSchema(BaseModel):
    """The JSON object the model must return (strict: no type coercion, no extra keys)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    action: str
    entity: str
    end_signal: bool
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    recipient: str = ""      # required for send_message, optional ("") for the other actions

    @model_validator(mode="after")
    def _check_action(self):
        if self.action != "UNKNOWN" and self.action not in VALID_ACTIONS:
            raise ValueError(f"action {self.action!r} is not a configured action or UNKNOWN")
        if self.action == "send_message" and "recipient" not in self.model_fields_set:
            raise ValueError("send_message requires a recipient field")
        return self


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_response(text: str) -> dict:
    # Strip markdown fences if model ignores the "no fences" instruction
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    return json.loads(text.strip())


def _schema_error_text(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "response"
        parts.append(f"{loc}: {err['msg']}")
    return "schema error: " + "; ".join(parts)


def extract(client: OpenAI, clean_text: str, model: str = DEFAULT_MODEL,
            max_tokens: int = 500) -> LLMResult:
    """Call the LLM with the system prompt and return a structured LLMResult."""
    raw = ""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": clean_text},
            ],
            max_tokens=max_tokens,   # reasoning models spend this budget on thinking: 500 can cut them off
            temperature=0,
            response_format={"type": "json_object"},  # constrained decoding server-side
        )
        content = resp.choices[0].message.content
        if content is None:
            # Some models (reasoning/oss) return None content when they refuse or
            # put output in reasoning_content; treat as unrecognised command.
            raise ValueError("model returned null content")
        raw = content.strip()
    except Exception as e:
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response="", model=model, error=str(e), error_kind="api",
        )
    return parse_extraction(raw, model)


def parse_extraction(raw: str, model: str) -> LLMResult:
    """Turn the raw model answer into an LLMResult, or into a failure result
    (action UNKNOWN) if the answer is not JSON or breaks the schema."""
    try:
        parsed = _parse_response(raw)
    except json.JSONDecodeError as e:
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response=raw, model=model, error=f"JSON parse error: {e}",
            error_kind="syntax",
        )
    if not isinstance(parsed, dict):
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response=raw, model=model,
            error=f"schema error: response is a JSON {type(parsed).__name__}, not an object",
            error_kind="schema",
        )
    try:
        x = ExtractionSchema.model_validate(parsed)
    except ValidationError as e:
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response=raw, model=model, error=_schema_error_text(e),
            error_kind="schema",
        )
    return LLMResult(
        action=x.action, entity=x.entity, end_signal=x.end_signal,
        confidence=x.confidence, raw_response=raw, model=model,
        recipient=x.recipient,
    )


def select_model_interactive(client: OpenAI) -> str:
    """List available chat models and let user pick by number."""
    SKIP = ["embedding", "reranker", "stable-diffusion", "flux", "kokoro", "tts", "whisper", "stt"]
    models = [m.id for m in client.models.list().data
              if not any(k in m.id.lower() for k in SKIP)]
    print(f"\nAvailable models ({len(models)}):\n")
    for i, m in enumerate(models):
        current = "  <-- current" if m == DEFAULT_MODEL else ""
        print(f"  [{i+1}] {m}{current}")
    print()
    while True:
        try:
            idx = int(input(f"Select model [1-{len(models)}]: ").strip()) - 1
            if 0 <= idx < len(models):
                chosen = models[idx]
                _model_file.write_text(chosen)
                print(f"Using: {chosen}\n")
                return chosen
        except (ValueError, KeyboardInterrupt):
            print(f"Keeping current: {DEFAULT_MODEL}\n")
            return DEFAULT_MODEL


if __name__ == "__main__":
    client = get_client()
    model = select_model_interactive(client)
    test_cases = [
        "illustrate the patients medical history",
        "halt animation",
        "begin emergency",
        "activate display",
        "switch patient to mary johnson",
        "code red initiate",
        "diagnosis hemodialysis",
        "show blood pressure",  # post-abbreviation-expansion form
    ]
    for cmd in test_cases:
        r = extract(client, cmd, model=model)
        if r.error:
            print(f"ERROR ({cmd!r}): {r.error}")
        else:
            valid = "✓" if r.valid_action else "✗"
            print(f"[{valid}] {cmd!r:45s}  action={r.action!r:20s} entity={r.entity!r}")
