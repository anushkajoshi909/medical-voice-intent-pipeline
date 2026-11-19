# Evaluation

This folder holds everything for the evaluation of the pipeline: the scripts, the labels, the run folders, and the reports. The thesis (Chapter 7) describes the evaluation and its results. This file describes how it is organised and how to run it again.

## 1. What is evaluated

- **The dataset:** 243 commands, 130 valid and 113 invalid (`data/annotated_commands.json`).
- **The systems:** the baseline **B0** (Ludyga's grammar) and the pipeline with four language models: Llama 3.3 70B, Gemma 4 26B-A4B, gpt-oss-120b, and DeepSeek V4.1 Flash. gpt-oss-120b and DeepSeek run with 500 and with 4000 completion tokens, Llama and Gemma with 500.
- **The runs:** every model runs all 243 commands three times at temperature 0. B0 is deterministic and runs once. The whole evaluation was made twice, as a **first run** and a **second run**.
- **Not evaluated:** Stage 7 (confirmation), the speech recognition (all input is typed), and domain independence (no second domain was built).

## 2. Dataset and labels

All dataset files are in `data/`: the commands and their sources in `data/`, and the labels in `data/labels/`.

| File | Content |
|---|---|
| `data/annotated_commands.json` | the 243 commands with `valid`, `expected_action`, and `category` |
| `data/commands_200.json` | the original sentences of Ludyga |
| `data/labels/gold_labels.json` | the expected **entity** of 92 valid commands and the expected **end signal** of the 20 dictations, by command id |
| `data/labels/gold_labels_worksheet.csv` | the worksheet from which the gold labels were made |
| `data/labels/entity_resolution_gold.csv` | the set **E3**: 98 pairs of an action and an entity with the expected status, type, and concept |

**Commands:** 198 are the sentences of Ludyga (98 valid, 100 invalid). 45 were added (32 valid, 13 invalid): synonyms (18), abbreviations (10), entity lookup (4), other out-of-domain requests (5), entities of the wrong type (6), and abbreviations with entities of the wrong type (2).

**Gold labels:** the expected entity and end signal were written by hand and checked against the ontology files. The worksheet shows no model output, so the labels do not depend on the system. `make_gold_labels.py draft` writes the worksheet, and `make_gold_labels.py build` turns it into `gold_labels.json`. The scripts join the labels to the commands by id.

**E3:** the 98 pairs are exact names (39), synonyms (11), misspellings (14), entities of the wrong type (12), entities that are not in the ontologies (9), name collisions (7), and synonyms that are not in the ontologies (6). `check_entity_resolution_set.py` checks every row against the raw ontology files, without running any stage of the pipeline.

## 3. Folders

```
evaluation/
  evaluation.md                  this file
  scripts/                       the scripts (section 5); paths.py names the folders below
  final_run/                     the reported evaluation: 8 November 2026, final code (section 7)
    run_2026-11-08.log           the console log of the batch
    runs/<date>_<system>_<tag>_r<k>/   one folder for each run (never overwritten)
      results.jsonl              one line for each command: the labels, the outcome, the output of every stage,
                                 the raw answer of the model, and the time of every stage
      meta.json                  the record of the run: date, code version, tool versions, checksums of the
                                 configuration, grammar, and data files
      summary.json               the metrics computed from results.jsonl
    reports/final/               all_results, main_results and per_command of the run
    reports/tables/              overview_<date>_<tag>.csv (one row for each run folder)
  old_runs/                      earlier runs of 3 to 5 November 2026 (code before the fixes), kept unchanged
    runs/  reports/final/  reports/tables/  reports/figures/  logs/   (same layout)
```

New runs are written to `final_run/` by default (`--runs-dir` and `--reports-dir` choose another folder).

**Run folders:**
- final run (`final_run/runs/`): `2026-11-08_<model>_fixes500_r1` to `r3`, `2026-11-08_<model>__maxtok4000_fixes4000_r1` to `r3`, `2026-11-08_B0-ludyga_fixes500_r1`, `2026-11-08_E3_entity-resolution_fixes500`
- earlier runs (`old_runs/runs/`), first run: `2026-11-03_<model>_final_r1` to `r3` (six systems), `2026-11-03_B0-ludyga_final500_r1`, `2026-11-03_E3_entity-resolution`
- second run: `2026-11-04_<model>_final500_r1` to `r3`, `2026-11-05_<model>_final4000_r1` to `r3`, `2026-11-04_B0-ludyga_final500_r1`, `2026-11-05_E3_entity-resolution`

**Reports** (`reports/final/`, for each run): `all_results_<date>.json` has every metric with the definitions, `main_results_<date>.md` is the short report, and `per_command_<date>.csv` has one row for each command, system, and repetition. The final run is `final_run/reports/final/*_2026-11-08.*`. The earlier runs are `old_runs/reports/final/*_2026-11-03.*` (first) and `*_2026-11-05.*` (second), and `old_runs/reports/tables/results_summary.md` is an overview of these two runs with text.

## 4. Metrics and counting rules

The positive class is **valid**. Every command ends in one outcome, which comes from the status of Stage 6: `accepted` and `accepted_partial` give **ACCEPT**, `needs_clarification` gives **CLARIFY**, and `rejected` gives **REJECT**. Since November 2026 a new command without the trigger phrase gives `inactive` → **IGNORED**, which is never a correct decision (neither for a valid nor for an invalid command). The benchmark prepends the trigger to every command (section 7), so IGNORED should not occur in it.

| Metric | Definition |
|---|---|
| Accuracy (command validation accuracy) | (valid commands accepted + invalid commands rejected or clarified) / 243. A valid command that ends in CLARIFY is **not** a success. |
| Valid accepted (recall) | valid commands with ACCEPT / 130. Valid commands with CLARIFY and REJECT are reported as two further shares. |
| Invalid wrongly accepted (false-acceptance rate) | invalid commands with ACCEPT / 113 |
| Action classification accuracy (key `action_accuracy_pct`; called "action accuracy" in the reports up to November 2026) | valid commands with an action label whose extracted action equals `expected_action` / 130 |
| Entity extraction accuracy (normalised exact match) (key `entity_accuracy_pct`; earlier "entity accuracy") | valid commands with an entity label whose extracted entity **text** equals the label, ignoring case and punctuation except '/' / 92. Not entity-type classification, not NER F1, not ontology linking (that is E3). |
| End-signal F1 | precision and recall of the finishing phrase in the 20 dictations |
| Model error rate | calls without a usable answer (empty answer, answer cut off, invalid JSON, and since November 2026 an answer that breaks the Stage 2 schema) / calls |
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
python evaluation/scripts/run_eval.py --replay evaluation/final_run/runs/<folder> --no-baseline     # run the stages after the model on the stored answers, without model calls
python evaluation/scripts/run_e3.py [--tag T]                                             # entity resolution, no model; folder <date>_E3_entity-resolution[_T]

# reports
python evaluation/scripts/build_final_report.py --date 2026-11-08 --tags fixes500 fixes4000 --e3 2026-11-08_E3_entity-resolution_fixes500
python evaluation/scripts/build_final_report.py --date 2026-11-05 --also-dates 2026-11-04 --tags final500 final4000 \
    --runs-dir evaluation/old_runs/runs --out /tmp/old-report                               # an earlier run (the stored report stays as it is)
python evaluation/scripts/make_report_tables.py                                           # old_runs/reports/tables/results_summary.md (earlier runs)
python evaluation/scripts/make_figures.py                                                 # final_run/reports/figures/*.pdf,png (needs matplotlib), copied to the report
python evaluation/scripts/make_figures.py --out /tmp/figs --no-copy                       # the same, without touching the thesis folder

# offline tests of the pipeline (no model calls, no microphone, no waiting)
python3 -m unittest discover -s tests -v

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

## 7. Rerun after the fixes of November 2026

The fixes of Findings 7 to 12 (`docs/findings.md`) change the code of Stages 1, 2, 4, 5, 6 and 7, the scoring of ignored input, and the metric names. A fresh full run with the fixed code was made on 8 November 2026 (`final_run/`). It is the reported evaluation. The runs of 3 to 5 November, made before the fixes, are kept in `old_runs/`. The final run agrees with them: the accuracy of every system differs by at most about one percentage point, and the response times differ with the load of the model service. The B0 time stored in this run (median 0.016 s) is a measurement effect of that batch. The grammar code is unchanged, and timing it again on the 243 commands gives a median of about 0.1 ms, as in the earlier runs.

**Trigger preparation (same for the pipeline and B0).** The dataset commands contain no trigger phrase, because they test command recognition, not activation. The pipeline now ignores a new command without a trigger, so the benchmark gives every command the prefix "ok glasses " (`run_pipeline.with_trigger`), as the B0 runner has always done for Ludyga's grammar. The dataset files and the ids stay unchanged, the result rows keep the original text, and Stage 1 removes the trigger again, so the model sees the same text as before. Commands that already contain a trigger get no second one.

**Activation and interaction are tested separately.** The command-recognition benchmark (Stages 1 to 6) does not measure activation, clarification versus confirmation, or open dictations across turns. These are covered by the offline tests in `tests/` (99 tests: trigger variants, missing trigger with no model call, clarification under all confirmation policies, open dictation with reminder, finish, continue, cancel and silence, typing across the reminder and discard deadlines, pasted lines and split UTF-8 characters, pasted text never answering a confirmation, one deadline per confirmation question, compound medications, schema). Ignored input is never scored as a correct recognition.

**When replay is valid.** `run_eval.py --replay` runs Stages 1 and 3 to 6 on the stored Stage 2 answers. It is valid for measuring a change of the stages after the model, with the same model answers. It is not enough for the final results after the fixes: the schema now decides what a usable answer is, and a model's answers vary between runs. A replay of the first run with the fixed code (written to a scratch folder, 8 November 2026) gave 0 changed outcomes in 4,374 results and 0 ignored commands. A new B0 run gave 0 changed outcomes in 243. All 8,465 stored model answers parse to the same Stage 2 result under the new schema. The final numbers still come from a fresh run.

**Commands of the final run.** New tags keep the new runs apart from the earlier batches; the earlier run folders stay untouched.

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests                       # must pass first
python evaluation/scripts/run_eval.py --models google/gemma-4-26B-A4B-it --reps 1 --sample 12 \
    --tag fixes-smoke --runs-dir /tmp/fixes-smoke             # smoke test with one model, outside evaluation/final_run
python evaluation/scripts/run_all.py --tag500 fixes500 --tag4000 fixes4000   # full batch, about 2 to 4 hours
# if the batch ran over midnight:
python evaluation/scripts/build_final_report.py --date <second date> --also-dates <first date> --tags fixes500 fixes4000
```

`run_all.py` runs E3 with the 500-token tag (folder `<date>_E3_entity-resolution_fixes500`) and passes that folder to `build_final_report.py` with `--e3`, so the report never picks up the E3 result of another batch. If the batch runs over midnight, rebuild the report by hand with `--date <second date> --also-dates <first date> --tags fixes500 fixes4000 --e3 <date>_E3_entity-resolution_fixes500`.

No prompt, threshold, grammar, or ontology was changed or tuned against the evaluation commands for these fixes.

## 8. History

The earlier plan of this file (experiments E1 to E6, work order, checklists) is no longer needed and was removed. The development notes about false accepts are in `docs/findings.md`, with a status table of what is still true. The earlier version of this file is in the git history.
