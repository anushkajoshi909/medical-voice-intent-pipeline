# Main results (2026-11-05 runs)

Written by evaluation/scripts/build_final_report.py. 243 commands (130 valid, 113 invalid); mean ± SD over 3 repetitions; percent unless noted. All other numbers and the definition of each metric are in all_results_2026-11-05.json.

## 1. Overall result of the pipeline

- **Overall accuracy:** commands decided correctly, out of 243: valid commands accepted plus invalid commands rejected or sent to clarification.
- **Valid accepted:** share of the 130 valid commands that the pipeline accepts.
- **Invalid wrongly accepted:** share of the 113 invalid commands that the pipeline accepts.
- **Latency:** seconds per command from raw text to the structured result (remote model; depends on load).

| system | overall accuracy | valid accepted | invalid wrongly accepted | latency median | latency p95 |
|---|---|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 90.2 ± 0.2 | 82.8 ± 1.2 | 1.2 ± 1.0 | 1.04 ± 0.03 | 2.62 ± 0.14 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 93.1 ± 0.2 | 88.7 ± 0.4 | 1.8 ± 0.0 | 1.09 ± 0.01 | 3.78 ± 0.19 |
| google/gemma-4-26B-A4B-it | 95.8 ± 0.2 | 93.6 ± 0.4 | 1.8 ± 0.0 | 0.77 ± 0.01 | 2.26 ± 0.95 |
| meta-llama/Llama-3.3-70B-Instruct | 95.1 ± 0.8 | 97.2 ± 0.9 | 7.4 ± 0.5 | 0.99 ± 0.04 | 1.82 ± 0.25 |
| openai/gpt-oss-120b | 93.5 ± 0.8 | 90.3 ± 1.6 | 2.7 ± 0.0 | 1.51 ± 0.05 | 5.31 ± 0.27 |
| openai/gpt-oss-120b__maxtok4000 | 94.2 ± 0.8 | 91.8 ± 1.6 | 3.0 ± 0.5 | 1.54 ± 0.00 | 5.63 ± 1.19 |
| B0 Ludyga grammar | 70.8 | 46.2 | 0.9 | 0.0001 | 0.0005 |

## 2. What the language model extracts (Stage 2)

Measured against the gold labels: the action of the 130 valid commands, the entity of 92 of them, and the end signal of the 20 dictations.

| system | action accuracy | entity accuracy | end-signal F1 |
|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 86.4 ± 0.9 | 83.3 ± 1.3 | 72.5 ± 2.8 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 92.5 ± 1.2 | 85.2 ± 0.6 | 100.0 ± 0.0 |
| google/gemma-4-26B-A4B-it | 98.2 ± 0.5 | 90.2 ± 0.0 | 100.0 ± 0.0 |
| meta-llama/Llama-3.3-70B-Instruct | 96.2 ± 1.3 | 85.1 ± 2.5 | 99.0 ± 1.7 |
| openai/gpt-oss-120b | 91.0 ± 1.2 | 82.6 ± 2.9 | 91.4 ± 4.1 |
| openai/gpt-oss-120b__maxtok4000 | 93.8 ± 1.3 | 86.2 ± 2.7 | 99.0 ± 1.7 |

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
