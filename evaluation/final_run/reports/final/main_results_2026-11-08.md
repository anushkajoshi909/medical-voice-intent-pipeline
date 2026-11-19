# Main results (2026-11-08 runs)

Written by evaluation/scripts/build_final_report.py. 243 commands (130 valid, 113 invalid); mean ± SD over 3 repetitions; percent unless noted. All other numbers and the definition of each metric are in all_results_2026-11-08.json.

## 1. Overall result of the pipeline

- **Overall accuracy:** commands decided correctly, out of 243: valid commands accepted plus invalid commands rejected or sent to clarification.
- **Valid accepted:** share of the 130 valid commands that the pipeline accepts.
- **Invalid wrongly accepted:** share of the 113 invalid commands that the pipeline accepts.
- **Latency:** seconds per command from raw text to the structured result (remote model; depends on load).

| system | overall accuracy | valid accepted | invalid wrongly accepted | latency median | latency p95 |
|---|---|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 91.1 ± 0.3 | 84.9 ± 0.5 | 1.8 ± 0.0 | 1.03 ± 0.00 | 3.73 ± 0.31 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 93.0 ± 0.0 | 88.5 ± 0.0 | 1.8 ± 0.0 | 0.92 ± 0.05 | 3.28 ± 0.58 |
| google/gemma-4-26B-A4B-it | 95.8 ± 0.2 | 93.6 ± 0.4 | 1.8 ± 0.0 | 0.40 ± 0.08 | 0.94 ± 0.14 |
| meta-llama/Llama-3.3-70B-Instruct | 95.4 ± 0.2 | 96.7 ± 0.4 | 6.2 ± 0.0 | 0.69 ± 0.13 | 1.27 ± 0.16 |
| openai/gpt-oss-120b | 93.8 ± 0.4 | 91.0 ± 0.4 | 3.0 ± 0.5 | 1.09 ± 0.08 | 4.67 ± 0.18 |
| openai/gpt-oss-120b__maxtok4000 | 94.8 ± 0.5 | 93.3 ± 0.4 | 3.5 ± 0.8 | 1.08 ± 0.02 | 4.91 ± 0.74 |
| B0 Ludyga grammar | 70.8 | 46.2 | 0.9 | 0.0161 | 0.0193 |

## 2. What the language model extracts (Stage 2)

Measured against the gold labels: the action of the 130 valid commands with an action label, the entity text of the 92 with an entity label, and the end signal of the 20 dictations.

| system | Action classification accuracy | Entity extraction accuracy (normalised exact match) | End-signal F1 |
|---|---|---|---|
| deepseek-ai/DeepSeek-V4.1-Flash | 87.4 ± 0.5 | 83.0 ± 1.7 | 72.5 ± 2.8 |
| deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000 | 92.8 ± 1.2 | 85.2 ± 0.6 | 100.0 ± 0.0 |
| google/gemma-4-26B-A4B-it | 98.5 ± 0.0 | 89.1 ± 1.9 | 100.0 ± 0.0 |
| meta-llama/Llama-3.3-70B-Instruct | 97.4 ± 0.5 | 86.9 ± 1.9 | 99.0 ± 1.7 |
| openai/gpt-oss-120b | 93.6 ± 1.2 | 84.1 ± 2.3 | 90.3 ± 0.0 |
| openai/gpt-oss-120b__maxtok4000 | 96.2 ± 0.8 | 85.5 ± 1.7 | 97.0 ± 0.0 |

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
