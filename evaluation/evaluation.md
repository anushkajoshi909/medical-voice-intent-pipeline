# Evaluation

This folder holds everything for the evaluation of the pipeline: the scripts, the labels, the run folders, and the reports. The thesis (Chapter 7) describes the evaluation and its results. This file describes how it is organised and how to run it again.

## 1. What is evaluated

- **The dataset:** 243 commands, 130 valid and 113 invalid (`data/annotated_commands.json`).
- **The systems:** the baseline **B0** (Ludyga's grammar) and the pipeline with four language models: Llama 3.3 70B, Gemma 4 26B-A4B, gpt-oss-120b, and DeepSeek V4.1 Flash. gpt-oss-120b and DeepSeek run with 500 and with 4000 completion tokens, Llama and Gemma with 500.
- **The runs:** every model runs all 243 commands three times at temperature 0. B0 is deterministic and runs once. The whole evaluation was made twice, as a **first run** and a **second run**.
- **Not evaluated:** Stage 7 (confirmation), the speech recognition (all input is typed), and domain independence (no second domain was built).

## 2. Dataset and labels

| File | Content |
|---|---|
| `data/annotated_commands.json` | the 243 commands with `valid`, `expected_action`, and `category` |
| `data/commands_200.json` | the original sentences of Ludyga |
| `evaluation/dataset/gold_labels.json` | the expected **entity** of 92 valid commands and the expected **end signal** of the 20 dictations, by command id |
| `evaluation/dataset/gold_labels_worksheet.csv` | the worksheet from which the gold labels were made |
| `evaluation/dataset/entity_resolution_gold.csv` | the set **E3**: 98 pairs of an action and an entity with the expected status, type, and concept |

**Commands:** 198 are the sentences of Ludyga (98 valid, 100 invalid). 45 were added (32 valid, 13 invalid): synonyms (18), abbreviations (10), entity lookup (4), other out-of-domain requests (5), entities of the wrong type (6), and abbreviations with entities of the wrong type (2).

**Gold labels:** the expected entity and end signal were written by hand and checked against the ontology files. The worksheet shows no model output, so the labels do not depend on the system. `make_gold_labels.py draft` writes the worksheet, and `make_gold_labels.py build` turns it into `gold_labels.json`. The scripts join the labels to the commands by id.

**E3:** the 98 pairs are exact names (39), synonyms (11), misspellings (14), entities of the wrong type (12), entities that are not in the ontologies (9), name collisions (7), and synonyms that are not in the ontologies (6). `check_entity_resolution_set.py` checks every row against the raw ontology files, without running any stage of the pipeline.

## 3. Folders

```
evaluation/
  evaluation.md                  this file
  dataset/                       the labels (section 2)
  scripts/                       the scripts (section 5)
  runs/<date>_<system>_<tag>_r<k>/   one folder for each run (never overwritten)
      results.jsonl              one line for each command: the labels, the outcome, the output of every stage,
                                 the raw answer of the model, and the time of every stage
      meta.json                  the record of the run: date, code version, tool versions, checksums of the
                                 configuration, grammar, and data files
      summary.json               the metrics computed from results.jsonl
  reports/final/                 the reports of each run
  reports/tables/                overview_<date>.csv (one row for each run folder) and results_summary.md
```

**Run folders:**
- first run: `2026-11-03_<model>_final_r1` to `r3` (six systems), `2026-11-03_B0-ludyga_final500_r1`, `2026-11-03_E3_entity-resolution`
- second run: `2026-11-04_<model>_final500_r1` to `r3`, `2026-11-05_<model>_final4000_r1` to `r3`, `2026-11-04_B0-ludyga_final500_r1`, `2026-11-05_E3_entity-resolution`

**Reports** (`reports/final/`, for each run): `all_results_<date>.json` has every metric with the definitions, `main_results_<date>.md` is the short report, and `per_command_<date>.csv` has one row for each command, system, and repetition. The first run is `*_2026-11-03.*` and the second run is `*_2026-11-05.*`. `reports/tables/results_summary.md` is an overview of both runs with text.

## 4. Metrics and counting rules

The positive class is **valid**. Every command ends in one outcome, which comes from the status of Stage 6: `accepted` and `accepted_partial` give **ACCEPT**, `needs_clarification` gives **CLARIFY**, and `rejected` gives **REJECT**.

| Metric | Definition |
|---|---|
| Accuracy (command validation accuracy) | (valid commands accepted + invalid commands rejected or clarified) / 243. A valid command that ends in CLARIFY is **not** a success. |
| Valid accepted (recall) | valid commands with ACCEPT / 130. Valid commands with CLARIFY and REJECT are reported as two further shares. |
| Invalid wrongly accepted (false-acceptance rate) | invalid commands with ACCEPT / 113 |
| Action accuracy | valid commands whose extracted action equals `expected_action` |
| Entity accuracy | the extracted entity equals the gold entity, ignoring case and punctuation (the model sometimes writes punctuation back into the entity) |
| End-signal F1 | precision and recall of the finishing phrase in the 20 dictations |
| Model error rate | calls without a usable answer (empty answer, answer cut off, invalid JSON) / calls |
| Response time | seconds from the text to the result: median, 95th percentile, share within 1 s |
| LLM alone | the decision of Stage 2 only: ACCEPT if the action is valid, without Stages 3 to 5. It shows what the checks add. |

**Counting rules:** an error of the model, a timeout, or invalid JSON gives REJECT and is also counted in the model error rate. An input that is not speech gives REJECT. A command that fails the grammar gives REJECT. Results are the mean and the standard deviation over the three repetitions. There are no confidence intervals or significance tests, and the small categories are reported as counts.

**Criteria** (from Ludyga): accuracy of at least 75 % and a response time of at most one second.

## 5. How to run

The scripts read the API key from `~/.scadsai-api-key`, and they run from the project folder.

```bash
# the whole batch: 500-token runs, 4000-token runs, B0, E3, and the report (about 2 to 4 hours, resumable on the same day)
python evaluation/scripts/run_all.py
python evaluation/scripts/run_all.py --skip-llm        # only E3 and the report, from existing runs

# single steps
python evaluation/scripts/run_eval.py --models openai/gpt-oss-120b --reps 1 --tag test    # one system
python evaluation/scripts/run_eval.py --models openai/gpt-oss-120b --max-tokens 4000 --no-baseline --tag final4000
python evaluation/scripts/run_eval.py --replay evaluation/runs/<folder> --no-baseline     # run the stages after the model on the stored answers, without model calls
python evaluation/scripts/run_e3.py                                                       # entity resolution, no model

# reports
python evaluation/scripts/build_final_report.py --date 2026-11-05 --also-dates 2026-11-04 --tags final500 final4000
python evaluation/scripts/make_report_tables.py                                           # reports/tables/results_summary.md

# labels
python evaluation/scripts/make_gold_labels.py draft      # write the worksheet
python evaluation/scripts/make_gold_labels.py build      # build gold_labels.json
python evaluation/scripts/check_entity_resolution_set.py # check E3 against the ontology files
```

`run_eval.py` skips a run folder of the same date that is complete, so an interrupted batch continues when it is started again on the same day. A batch that runs over midnight has folders of two dates, and `--also-dates` of `build_final_report.py` joins them.

## 6. Limits

- One dataset of 243 commands, written for the system and not taken from real use, with typed input.
- The gold labels were made by one person. A second person should check a sample.
- The model service is shared and remote. The answers can differ between two runs even at temperature 0, and the response times depend on its load. This is why the evaluation was made twice.
- Domain independence is argued from the design and is not measured.

## 7. History

The earlier plan of this file (experiments E1 to E6, work order, checklists) is no longer needed and was removed. The development notes about false accepts are in `docs/findings.md`, with a status table of what is still true. The earlier version of this file is in the git history.
