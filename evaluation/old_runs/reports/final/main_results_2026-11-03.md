# Main results (2026-11-03 runs)

Written by evaluation/scripts/build_final_report.py. 243 commands (130 valid, 113 invalid); mean ± SD over 3 repetitions; percent unless noted. All other numbers and the definition of each metric are in all_results_2026-11-03.json.

## 1. Overall result of the pipeline

- **Overall accuracy:** commands decided correctly, out of 243: valid commands accepted plus invalid commands rejected or sent to clarification.
- **Valid accepted:** share of the 130 valid commands that the pipeline accepts.
- **Invalid wrongly accepted:** share of the 113 invalid commands that the pipeline accepts.
- **Latency:** seconds per command from raw text to the structured result (remote model; depends on load).

| system | overall accuracy | valid accepted | invalid wrongly accepted | latency median | latency p95 |
|---|---|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 90.1 ± 0.0 | 83.1 ± 0.0 | 1.8 ± 0.0 | 1.26 ± 0.09 | 3.82 ± 0.45 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 92.7 ± 0.8 | 88.0 ± 1.6 | 1.8 ± 0.0 | 0.94 ± 0.01 | 3.68 ± 0.13 |
| google/gemma-4-26B-A4B-it | 95.9 ± 0.0 | 93.8 ± 0.0 | 1.8 ± 0.0 | 0.37 ± 0.13 | 1.18 ± 0.56 |
| meta-llama/Llama-3.3-70B-Instruct | 95.1 ± 0.0 | 96.9 ± 0.0 | 7.1 ± 0.0 | 0.64 ± 0.02 | 1.09 ± 0.01 |
| openai/gpt-oss-120b | 93.0 ± 0.8 | 89.8 ± 1.2 | 3.2 ± 0.5 | 1.55 ± 0.03 | 5.90 ± 1.04 |
| openai/gpt-oss-120b__maxtok4000 | 95.4 ± 0.2 | 93.6 ± 0.4 | 2.7 ± 0.0 | 1.13 ± 0.10 | 6.13 ± 1.78 |
| B0 Ludyga grammar | 70.8 | 46.2 | 0.9 | 0.0001 | 0.0005 |

## 2. What the language model extracts (Stage 2)

Measured against the gold labels: the action of the 130 valid commands, the entity of 92 of them, and the end signal of the 20 dictations.

| system | action accuracy | entity accuracy | end-signal F1 |
|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 85.9 ± 0.5 | 84.1 ± 1.7 | 69.1 ± 5.0 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 91.8 ± 0.5 | 82.6 ± 2.2 | 98.0 ± 1.7 |
| google/gemma-4-26B-A4B-it | 98.5 ± 0.0 | 89.8 ± 0.6 | 100.0 ± 0.0 |
| meta-llama/Llama-3.3-70B-Instruct | 98.5 ± 0.0 | 84.1 ± 0.6 | 100.0 ± 0.0 |
| openai/gpt-oss-120b | 93.1 ± 1.6 | 85.2 ± 1.3 | 89.1 ± 2.1 |
| openai/gpt-oss-120b__maxtok4000 | 94.9 ± 0.5 | 84.4 ± 1.3 | 100.0 ± 0.0 |

Latency of deepseek-ai/DeepSeek-V4.1-Flash, deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000, google/gemma-4-26B-A4B-it, meta-llama/Llama-3.3-70B-Instruct, openai/gpt-oss-120b, openai/gpt-oss-120b__maxtok4000 is the Stage 2 time of the original run; Stages 1 and 3-6 were re-run.

## 3. Entity resolution and type check (Stages 4-5, no LLM)

98 (action, entity) pairs. Status = accepted, type mismatch, or not found, as expected. Full = also the right type and concept.

| group | pairs | status correct | full correct |
|---|---|---|---|
| overall | 98 | 92.9 | 86.7 |
| collision | 7 | 57.1 | 0.0 |
| exact | 39 | 100.0 | 97.4 |
| misspelling | 14 | 100.0 | 100.0 |
| not_in_ontology | 9 | 100.0 | 100.0 |
| synonym | 11 | 100.0 | 100.0 |
| unseen_synonym | 6 | 33.3 | 16.7 |
| wrong_type | 12 | 100.0 | 100.0 |
