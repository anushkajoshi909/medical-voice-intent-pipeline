#!/usr/bin/env python3
"""Stage 2: LLM Structured Extraction

Input:  clean_text from Stage 1 (trigger stripped, abbreviations expanded)
Output: LLMResult dataclass with action, entity, end_signal, confidence

LLM selection rationale (SCADS.AI, tested 2026-07):
  alias-ha  = Llama 3.3 70B Instruct — chosen for production.
              Best speed/quality tradeoff; 70B models are well-established
              for reliable structured JSON output and medical NL understanding.
  alias-reasoning = thinking model (slower, ~10x latency).
              Used only for thesis evaluation comparison, not real-time pipeline.
  Llama-3.1-8B = too small; inconsistent JSON formatting on edge cases.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
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
    error: str | None = None

    @property
    def valid_action(self) -> bool:
        return self.action in VALID_ACTIONS


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_response(text: str) -> dict:
    # Strip markdown fences if model ignores the "no fences" instruction
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    return json.loads(text.strip())


def extract(client: OpenAI, clean_text: str, model: str = DEFAULT_MODEL) -> LLMResult:
    """Call the LLM with the system prompt and return a structured LLMResult."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": clean_text},
            ],
            max_tokens=500,
            temperature=0,
            response_format={"type": "json_object"},  # constrained decoding server-side
        )
        content = resp.choices[0].message.content
        if content is None:
            # Some models (reasoning/oss) return None content when they refuse or
            # put output in reasoning_content; treat as unrecognised command.
            raise ValueError("model returned null content")
        raw = content.strip()
        parsed = _parse_response(raw)
        return LLMResult(
            action=str(parsed.get("action", "UNKNOWN")),
            entity=str(parsed.get("entity", "")),
            end_signal=bool(parsed.get("end_signal", False)),
            confidence=float(parsed.get("confidence", 0.0)),
            raw_response=raw,
            model=model,
        )
    except json.JSONDecodeError as e:
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response=raw if "raw" in dir() else "",
            model=model, error=f"JSON parse error: {e}",
        )
    except Exception as e:
        return LLMResult(
            action="UNKNOWN", entity="", end_signal=False, confidence=0.0,
            raw_response="", model=model, error=str(e),
        )


def _select_model_interactive(client: OpenAI) -> str:
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
    model = _select_model_interactive(client)
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
