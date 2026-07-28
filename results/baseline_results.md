# Ludyga Baseline — Results Summary

Environment: `.venv` (Python 3.12), `antlr4-python3-runtime==4.13.1`, baseline cloned
from [tomlud123/ANTLR-healthcare-speech](https://github.com/tomlud123/ANTLR-healthcare-speech)
into `baseline_ludyga/`. Test set cloned from the repo wiki
(`baseline_ludyga_wiki/Language-samples.md`) into `data/commands_200.json`.

Note: the wiki labels the valid-command sections "20x" each, but the
`u13-u14` section actually lists 18 items, not 20. Test set is therefore
**198 commands** (100 invalid, 98 valid), not 200.

## Scores (`src/run_baseline.py` → `results/baseline_results.json`)

| | Count | Result |
|---|---|---|
| Overall accuracy | 198 | 149/198 = **75.3%** |
| Invalid commands correctly rejected | 100 | 100/100 = **100%** |
| Valid commands correctly accepted | 98 | 49/98 = **50.0%** |

This matches the expected profile of a hand-written ANTLR grammar: it is a
**precise but brittle** recognizer — zero false accepts of garbage/ambiguous/
out-of-domain input, but it rejects half of legitimate paraphrases because it
only matches exact tokens the grammar author anticipated.

## Failure catalogue (`results/failure_catalogue.md`)

49 valid commands fail to parse, split:
- **30 vocabulary gaps** — synonyms/paraphrases not in the grammar's token
  list (e.g. "illustrate" instead of "show", "begin emergency" instead of
  "start emergency", "switch patient" instead of "change patient").
- **19 syntax gaps** — correct vocabulary but different word order, added
  filler, or punctuation the grammar rule doesn't accept (e.g. "Display on",
  "turn it off", "Stop now frame").

Zero false accepts — the 100 garbage/ambiguous/out-of-domain commands were
all correctly rejected. This is the baseline this thesis aims to improve on
recall (valid-command acceptance) **without** sacrificing precision
(false-accept rate must stay at 0%).

## Whisper ASR integration (`src/whisper_asr.py`)

Upstream ASR component wired to the SCADS.AI-hosted `alias-stt` (resolves to `openai/whisper-large-v3-turbo`; originally `openai/whisper-large-v3` but team access was revoked)
model via the OpenAI-compatible client. `transcribe(client, audio_path)` is
the integration point to be placed in front of the grammar/LLM pipeline.

Verified on 4 round-trip phrases (local `say`+`afconvert` TTS → Whisper, since
SCADS.AI's own TTS endpoints `tts-1-hd`/`Kokoro-82M` returned malformed/empty
audio at test time): transcription was accurate to the word for all 4,
including the `hemodialysis`/`hemolysis` pair the thesis's central use case
depends on. No bulk ASR run was performed over the 198-command set — by
design, see decision below.

**Scope decision:** the 198-command baseline reproduction above runs on the
raw text commands directly (matching Ludyga's own published evaluation
1:1). Whisper is integrated and demonstrated as a working component, but not
run over all 198 commands through TTS, to keep the baseline numbers directly
comparable to the original thesis [3] and avoid synthetic-TTS noise that
isn't representative of real clinical speech.

## Files

```
Thesis_Code/
  .venv/                          — Python env (antlr4-python3-runtime, openai)
  baseline_ludyga/                — clone of tomlud123/ANTLR-healthcare-speech
  baseline_ludyga_wiki/           — clone of the repo wiki (test set source)
  data/commands_200.json          — parsed 198-command test set, labelled valid/invalid
  src/parse_commands.py           — wiki markdown -> commands_200.json
  src/run_baseline.py             — runs commands through Ludyga's grammar, scores
  src/build_failure_catalogue.py  — generates failure_catalogue.md
  src/whisper_asr.py              — Whisper ASR integration (SCADS.AI)
  results/baseline_results.json   — per-command raw results
  results/failure_catalogue.md    — categorized failures
  results/baseline_results.md     — this file
```
