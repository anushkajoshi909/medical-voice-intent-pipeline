# Evaluation Plan

*Improving Intent Recognition in Grammar-Based Voice Interfaces — Anushka Joshi, TU Dresden. Deadline 2 Dec 2026.*

**Status: plan only. No experiment in this document has been run for the thesis; every result is "not yet measured".** Numbers quoted from the repository are earlier single runs (see §9) and must not be cited as final.

Legend: **[V]** verified in the repository · **[P]** proposed, does not exist yet.

---

## 1. Scope

The solution is fixed (7-stage pipeline, `Final_Pipeline_Complete.md`). The evaluation shows **that it works, what each part contributes, what it costs, and that the requirements are met** — nothing more. Alternatives raised at the midterm (stage order, knowledge graph, other knowledge sources) are treated in the state-of-the-art and discussion chapters, not as experiments (§8).

Inherited vs. new, for the report (professors asked for this explicitly):

| Component | Ludyga (2024) | This thesis | External |
|---|---|---|---|
| Grammar (`.g4`), command types u01–u20, 198 test sentences | ✔ | extended: diagnosis (u21), start-treatment (u22), `stop [entity]` (`grammar_extend.md`) | ANTLR4 |
| Stage 1 preprocessing, Stage 2 LLM extraction + domain gating, Stage 4 ontology resolution, Stage 5 semantic check, Stage 6 intent/explanation, Stage 7 confirmation, `domain_config.yaml`, `domain_loader.py` | — | new | OpenAI-style client, Llama etc. via SCADS.AI; DOID, HPO, NCIt, RxNorm |
| Stage 3 ANTLR validation | grammar | new runner that parses a canonical string rebuilt from the LLM output | ANTLR runtime |
| ASR (Whisper) | — | implementation choice, out of evaluation scope | Whisper |

Note for the report: the LLM is **also a validity gate** (returns `UNKNOWN` for non-clinical/vague input, `domain_config.yaml::domain_rules`). For several input classes (garbage, out-of-domain, free text) it is the only component that can reject. State this in the pipeline chapter and in the responsibility table.

---

## 2. Evaluation questions (mapped to the thesis RQs)

Midterm RQs (slide 13) plus the general question Lidia suggested ("how can voice-control flexibility and interpretability be improved?") that the specific RQs refine.

| RQ | Evaluation question | Item (§4) |
|---|---|---|
| RQ1 LLM intent extraction outside grammar vocabulary | Does the pipeline accept paraphrases/synonyms/abbreviations that Ludyga's grammar rejects, without accepting more invalid input? | E1, E2 |
| RQ2 Entity and type identification via ontologies | Are entities resolved to the right concept and type; are type mismatches (`diagnosis hemodialysis`) caught? | E3 |
| RQ3 Ontology grounding | Is every accepted entity grounded with an ID and source; what happens for unknown entities? | E3, E6 |
| RQ4 Effect of LLM choice | How do accuracy and latency change across 2–3 LLMs? | E4 |
| General: flexibility *and* reliability/explainability, at what cost | Latency per stage; explanation completeness; effort to extend | E5, E6 |

---

## 3. Requirements → evidence

Requirement names are those on slide 16 and in the task description (`Anushka_MA_Aufgabenstellung-2.pdf`). The task description defines no numeric thresholds, so **we do not set any: we report the measured metrics and discuss in the text how they relate to each requirement** (the "Threshold" column is therefore "report metric").

| Requirement | Metric | How it is shown | Existing support | Threshold |
|---|---|---|---|---|
| FR Intent extraction | action accuracy; entity accuracy; end-signal P/R/F1 | E1/E2 on valid commands | action accuracy only [V]; entity/end-signal gold labels missing | report metric |
| FR Structural validation | structural-error detection (e.g. dose missing); false rejections caused by Stage 3 | E2 per-stage attribution + targeted items | Stage 3 works [V]; false-rejection defect known (§9) | report metric |
| FR Entity resolution | resolution accuracy; coverage | E3 | partial (type known, concept gold missing) | report metric |
| FR Semantic validation | type-mismatch detected / incorrectly accepted | E3 | 8 type-mismatch items [V] | report metric |
| FR Explainable output & confirmation | trace completeness; provenance coverage; confirmation path exists | E6 | `explanation` block in Stage 6 [V], Stage 7 [V] | report metric |
| NFR Reliability & robustness | false-acceptance rate, false-rejection rate; defined behaviour on LLM error/timeouts; run-to-run agreement | E1, E2, repeated runs | LLM errors → `UNKNOWN` [V]; runs differ at temperature 0 [V] | report metric |
| NFR Explainability | as FR above | E6 | partial | report metric |
| NFR Performance | end-to-end and per-stage latency (median, p95), cold vs warm, LLM calls/command | E5 | **none** (total wall-clock only) | report metric |
| NFR Domain independence | which files/code must change for a new domain (audit) | E6c + audit | config-driven but `_canonical()` in `stage3_antlr_validate.py` is hard-coded medical [V] | qualitative |
| NFR Maintainability & extensibility | files/LOC changed to add one action; regression results unchanged | E6c | none | qualitative |
| (optional) Availability/offline, resource limits | — | discussed, **not met** (Stage 2 needs a remote LLM; large ontologies in memory) | — | — |

---

## 4. Experiments

All run on the same dataset (§5). Each run is repeated **3 times** because the LLM is not reproducible even at `temperature=0` (§9).

### E1 — Baselines against the hybrid (RQ1, "why hybrid?")
- **B0 Ludyga grammar:** `src/run_baseline.py` (calls `baseline_ludyga/commandToJson.py`) [V]. Earlier result 49/98 valid accepted, 100/100 invalid rejected (`results/baseline_results.md`). Re-run once to confirm the checkout still works; if it does not, say so — never relabel a reconstruction as B0.
- **B1 Hybrid (full pipeline):** `src/run_pipeline.py` [V].
- **B2 LLM-only:** decision = Stage 2 only (`action != UNKNOWN`). Computed **offline from the stored per-stage results — no new code** [P, trivial].
- *(Optional, only if time)* B3 grammar-only on the thesis grammar: parse the raw text directly, no LLM. Cheap (one small script) and answers "does extending the grammar by hand close the gap?" — which the task description explicitly asks about ("beyond manual grammar extension").
- **Outputs:** valid recall, false-acceptance rate (FAR), false-rejection rate, overall accuracy, per-category table.

### E2 — What each stage contributes (RQ1, RQ2, "which problem does each component solve")
Offline analysis of stored results: for every invalid command, record which stage rejected it (Stage 1 format / LLM `UNKNOWN` / Stage 3 structure / Stage 5 type / none = false accept); for every valid command rejected, record which stage wrongly rejected it. Produces a **responsibility matrix** (failure class × stage). Replaces the old ablation code; nothing is switched off. Caveat to state: removing a stage and attributing a rejection to it are not identical, because a later stage might have caught the item too — report attribution as "first stage that rejected".

### E3 — Entity resolution and type checking (RQ2, RQ3)
Gold set of ~60–100 entities across: exact match, synonym, unseen term, ambiguous, wrong type for the action, not in ontology, misspelling/abbreviation. Metrics: resolution accuracy (concept/label), type accuracy, coverage, incorrect acceptance, incorrect rejection. Separate *retrieval* correctness (Stage 4) from *rule* correctness (Stage 5 given the right type). Known data issue to include as a finding: bare "hemolysis" is not in DOID/HPO and matches an NCIt lab test (`docs/findings.md` Finding 2).

**Built:** `evaluation/dataset/entity_resolution_gold.csv` (98 rows: 39 exact, 14 misspelling, 11 synonym, 12 wrong type, 9 not in ontology, 7 collision, 6 unseen synonym; actions diagnosis 34, start 22, set_medication 18, show 16, stop 8). Each row gives the entity as Stage 2 would pass it on, the expected status (`accepted` / `type_mismatch` / `entity_not_found`), the expected type and the expected concept id. The labels come from the raw ontology files, not from pipeline output; `scripts/check_entity_resolution_set.py` verifies every row against those files without running any stage. "Abbreviation" is not a category here, because Stage 1 expands abbreviations before Stage 2 (tested in E1/E2).

### E4 — LLM comparison (RQ4)
Same commands, same downstream stages, **4 models** chosen from the SCADS.AI list : `meta-llama/Llama-3.3-70B-Instruct` (current baseline), `google/gemma-4-26B-A4B-it` (smaller/faster), `openai/gpt-oss-120b` (larger), `deepseek-ai/DeepSeek-V4.1-Flash` (different family). Before the full runs, smoke-test each on ~10 commands (including a JSON-format failure case) to catch empty or malformed output. Metrics: action accuracy, entity accuracy, end-signal F1, FAR/FRR, JSON-parse failure rate, latency (E5). The set spans size (26B → 120B) and family so the accuracy/latency trade-off is visible.

### E5 — Performance and latency (NFR Performance)
Per-stage timing (`perf_counter`) for Stage 1, 2, 3, 4, 5, 6 and end-to-end; median, p95, mean; LLM calls per command; failure/timeout rate. Report **cold** (first call, includes ontology index build — Lidia flagged the large ontologies) separately from **warm**. Record network/endpoint, model, hardware. Present accuracy vs latency per model (E4).

### E6 — Explainability, requirements, extensibility
- **a) Explainability (decision-level, not LLM-internals).** Claim only traceability: the final decision can be traced to extracted intent/entity, grammar result, ontology ID + source, type rule, reason. Automatic metrics from the Stage 6 payload: *trace completeness* (all fields present per outcome class), *provenance coverage* (accepted entity slots carrying `ontology_id` and `source`), *reason coverage* (every reject/clarify has a reason code). Optional small human check (≈ 30 decisions, 2 raters): does the explanation state the real decisive reason? State plainly: this does not explain why the LLM chose an action.
- **b) Requirements table (§3)** completed with the evidence above.
- **c) Extensibility demonstration.** *(Lidia, exposé comment 10: for the domain-independence requirement a sequence diagram showing another domain with another ontology is sufficient — do that first; the live "add one action" demo below is the stronger, optional addition.)* Add **one** new action (chosen before starting) and log what had to change: `.g4` (+ regenerate), `domain_config.yaml`, any Python (Stage 3 `_canonical`), ontology file/loader, dataset items. Report files/LOC changed, config-only vs code, and that existing results are unchanged (replay stored Stage 2 outputs). Claim *design* domain independence plus this one demonstration; do **not** claim cross-domain generalisation without a second domain.

---

## 5. Dataset, ground truth, metrics

**Existing [V] — this is the dataset we use, not replaced:** `data/annotated_commands.json`, 243 items = 198 Ludyga sentences (`commands_200.json`; 100 invalid, 98 valid) + 45 thesis items (`ext_*`: synonyms 18, abbreviations 12, type mismatch 8, domain 5, ontology 4). Fields: `valid`, `expected_action` (all valid items), `category`, `stage2_valid` (8 type-mismatch items). 130 valid / 113 invalid. The docstring in `run_pipeline.py` (216 items, 18 extended) is stale.

**Missing [P]:**
1. Gold **entity** for valid items — only 18 items have any (`expected_entity*`). Needed for "entity accuracy" (slide 32). ~130 manual labels.
2. Gold **end_signal** for dictation items (`set_note`, `send_message`; ~20) — needed for end-signal P/R/F1.
3. Gold **outcome** per item: the *expected* result ACCEPT / CLARIFY / REJECT. The dataset only has `valid` true/false today. **Decided:** a valid command whose entity cannot be grounded (e.g. "Discontinue the antibiotics" → `entity_not_found` → `needs_clarification`) keeps ground truth *valid*, and the system outcome is reported as its own class: not counted as an accepted success and not as a false rejection.
4. **Held-out set (decided: yes):** a new set of ~30–50 commands with wording different from the development set, written after prompt/config/grammar are frozen and run once at the end; include entities containing grammar words ("chest pain at rest") to verify the Stage 3 fix. The 243 items were used while tuning the prompt and rules (few-shot contamination was found and fixed once, Finding 6), so results on them are labelled **development-set results**.

The new labels go in a **separate file keyed by item id** (`evaluation/dataset/gold_labels.json`: `id → {entity, end_signal, outcome}`), so `data/annotated_commands.json` stays untouched and nothing is duplicated; the scripts join the two files on `id`.

**Metrics (definitions fixed before running):**
- Action accuracy = correct action / valid commands. Entity accuracy = exact match after the same normalisation as Stage 1. End-signal P/R/F1 on dictation items.
- Positive class = valid. Recall = accepted valid / valid. FRR = 1 − recall. **FAR** = accepted invalid / invalid. Precision = valid among accepted. F1. (The code's `invalid_precision` is actually the invalid-rejection rate — rename in the thesis.)
- Counting rules: LLM error/timeout/bad JSON → REJECT *and* counted in a separate failure rate; format reject → REJECT; parser failure → REJECT (reason `invalid_structure`); valid commands ending in `needs_clarification` are reported as a separate row ("valid → clarification") next to accepted and rejected; valid recall counts only fully accepted commands; invalid commands ending in CLARIFY are reported as not accepted (and listed separately).
- Statistics: 3 runs per setting (mean ± SD); no confidence intervals or significance tests (decided: not needed); report counts for small categories (e.g. 8 type-mismatch items) instead of bare percentages.

---

## 6. Folder layout and run protocol

```
evaluation/
  evaluation.md                 this plan
  dataset/
    gold_labels.json            NEW gold fields per item id (entity, end_signal, outcome);
                                existing data/annotated_commands.json is used as-is and joined on id
    heldout_commands.json       optional fresh items (same format as annotated_commands.json)
    README.md                   label definitions, counting rules, how labels were made
  scripts/
    run_eval.py                 ONE script: runs B0 + the 4 LLMs x 3 reps, times every stage, scores each run
                                (hybrid, LLM-only B2, first-rejecting-stage, latency, explainability coverage),
                                writes runs/<id>/{results.jsonl,meta.json,summary.json} and reports/tables/overview_*.csv
                                (later, only if needed: separate plotting / significance-test scripts reading runs/)
  runs/                         raw output, one folder per run, NEVER edited afterwards
    2026-11-03_llama33-70b_full_r1/
      results.jsonl             one line per command: all stage outputs, raw LLM response, per-stage timings
      meta.json                 git commit, model id, dataset hash, domain_config/prompt/grammar hashes,
                                ontology file checksums, endpoint, date, python/lib versions, machine
    2026-11-03_llama33-70b_full_r2/ ...
    2026-11-04_<model2>_full_r1/ ...
    baseline_B0_ludyga/         results.json + meta.json
  reports/                      generated from runs/ only — safe to delete and regenerate
    tables/                     csv + md (one per thesis table)
    figures/                    png/pdf (accuracy-vs-latency, stage latency bars, category heatmap)
```

Rules: (1) `runs/` is append-only; (2) every table/figure in the thesis comes from a script in `scripts/` reading `runs/`, so any number can be regenerated; (3) one run folder = one model × one dataset × one repetition; (4) run name includes date, model, set, repetition; (5) commit the code and `meta.json` files, git-ignore bulky raw output only if it becomes large; (6) freeze prompt/config/grammar (git tag) before the final runs and put the tag in `meta.json`.

Model selection: today `--select` writes `.selected_model` and later runs silently reuse the last choice [V] (`src/stage2_llm_extract.py:31-32`, `src/select_model.py`). This hidden state makes runs non-reproducible. **Recommended small change [P]:** an explicit `--model <id>` argument that is always recorded in `meta.json`; drop the persisted file. Not done yet — needs your go-ahead because it touches pipeline code.

---

## 7. What exists vs. what to do

| Item | Status |
|---|---|
| B0 runner + earlier results | exists [V]; re-run to confirm |
| Hybrid run + per-stage fields saved | exists [V]; `--save` writes `results/pipeline_results_*.json`; does not save raw LLM response or timings |
| B2 (LLM-only) numbers | derivable offline from stored results — **no code** |
| Stage attribution (E2) | offline script — small |
| Per-stage latency, cold/warm | not implemented — small wrapper |
| `run_eval.py`, `score.py` | not implemented — small–medium |
| Gold entity / end-signal / outcome labels | not available — manual work (largest effort) |
| Entity-resolution gold set (E3) | not available — manual |
| LLM comparison | needs model list from the endpoint; only Llama 3.3 70B has saved results |
| Trace metrics | offline script on Stage 6 payload — small |
| Extensibility demo | not done |
| Automated tests | none |

---

## 8. Treated as discussion (state of the art / future work), not experiments

- **Stage order** (professor: ANTLR before Stage 2? ontology after Stage 4? parallel?). Argue from the data dependencies, which are verifiable in the code: Stage 3 builds its input from the LLM output (so it cannot precede Stage 2 as designed); ANTLR before the LLM means parsing the raw utterance (that is Ludyga's system, = B0/B3); Stages 3 and 4 are data-independent given Stage 2 (parallelisable) but Stage 3 is a cheap local call, so the gain is bounded by measured Stage 3 latency (E5) — can be stated with data. A grammar-first-with-LLM-fallback design is a reasoned future option (saves LLM calls on canonical commands; risk: the grammar rules `data`/`note` accept almost anything, so the LLM's domain gate would have to be replaced).
- **Knowledge graph, dictionary, embeddings, external terminology services** instead of ontologies: compare in the literature/discussion by what each needs and what it adds; current actions need label → concept → type lookups, which ontologies already provide; a graph adds relational checks (drug–indication) that no current action uses. E3 provides the evidence for the ontology step itself.
- **Grammar-constrained decoding** (Lidia's "outlines" comment): the repo uses server-side JSON mode only — not constrained by the ANTLR grammar. Future work.
- Second domain, on-device/offline operation, user study: future work.

---

## 9. Known issues to resolve or report before the final runs

1. **Runs are not reproducible at `temperature=0`:** three full runs gave 95.9 / 94.7 / 95.1 % binary accuracy (`docs/findings.md` Findings 4–5). Hence 3 repetitions, store raw responses, no best-run picking. The README's 95.9 % is one run.
2. **Stage 3 false rejections:** `validate("diagnosis","chest pain at rest")` and `("diagnosis","acute pain on exertion")` fail because grammar keywords (`at`, `on`, `of`, `to`, `and`, `new`, …) are not `WORD` tokens in `entity: (WORD|INT)+` [V, checked by calling the function]. **Decided fix:** inside Stage 3 only, parse a sanitised copy of the entity (keywords neutralised) for the structure check; the original entity goes unchanged to Stages 4–6 (removing words in Stage 1 would alter the ontology lookup and the text shown to the user). No grammar change. Verify on the held-out items; report how many development items were affected before/after. **Update 3 Nov 2026:** superseded for start, diagnosis and stop. The rule for start now has a fixed keyword (`START TREATMENT any_text`; Stage 3 rebuilds `start treatment <entity>`), and diagnosis and stop accept any words (`any_text`), so no keyword replacement is needed for them; replay of the stored Stage 2 outputs of 18 runs (4374 results) gave 0 changed results. The replacement was removed for `change_patient` on 4 Nov 2026 as well (its rule `patient_name` is Ludyga's and unchanged); Stage 3 recomputed on 3222 stored results: 0 differences.
3. **`diagnosis hemolysis` does not accept cleanly** (matches NCIt lab test "Hemolytic Index"; bare term not in DOID/HPO). The design file's `DOID:3680` is an obsolete oral-cancer term. Fix the example before using it on slides; use `set medication aspirin 100 mg` as running example and `diagnosis hemodialysis` as the safety catch.
4. **Known false accepts** (8 in the stored run, e.g. c049, c064, c078 via `show` → `ehr_record` passthrough, `docs/findings.md`). Report honestly; the old goal "false-accept rate 0 %" from `results/baseline_results.md` is not met.
5. `--text` mode mislabels correctness for dataset commands (Finding 2) — never use it for scoring.
6. README says GPT-4o/Groq were tested but `get_client()` only supports SCADS.AI — correct the README or the code.
7. Few-shot examples must never overlap evaluation items (scan again before final runs).
8. `Final_Pipeline_Complete.md` and `docs/uml_diagrams.md` claim the Stage 3 runner is domain-independent; `_canonical()` is hard-coded to this domain — qualify the claim.

---

## 10. Order of work (≈ 5 weeks left)

1. **Week 1:** labels (entity, end_signal, outcome) + `eval_set.json`; fix running example; decide §11 items; `--model` flag; re-run B0.
2. **Week 2:** `run_eval.py` with timings; Llama 3.3 70B × 3 runs; B2 and stage attribution offline; first latency tables.
3. **Week 3:** 3 further LLMs × 3 runs (E4); entity-resolution gold set (E3).
4. **Week 4:** explainability metrics; extensibility demo; statistics, tables, figures.
5. **Week 5:** write evaluation + discussion chapters; buffer. Writing runs in parallel from week 1.

**Cut order if late:** optional B3, human explanation check, third LLM, extensibility demo. Never cut: B0 vs hybrid, 3 repetitions, latency, requirements table.

---

## 11. Decisions needed from you / Lidia

1. ~~`entity_not_found` handling~~ — decided: valid + `needs_clarification` reported as its own class (§5).
2. ~~Stage 3 keyword defect~~ — decided: fix inside Stage 3 on a sanitised copy of the entity (§9.2).
3. ~~Held-out items~~ — decided: write a new set with different wording (§5).
4. ~~Models~~ — decided: Llama 3.3 70B, Gemma 4 26B-A4B, gpt-oss-120b, DeepSeek-V4.1-Flash (§4 E4).
5. ~~Thresholds~~ — decided: none; we report measured metrics only.
6. Which action to add for the extensibility demo?
7. OK to replace `.selected_model` with an explicit `--model` flag?

---

## 12. Checklist — presentation and report (from the midterm feedback)

**Final presentation (30 min; you used 20 and spoke too fast)**
- [ ] Merge "motivation" and "research problems" (Lidia: they repeat) — saves time for evaluation, conclusion, future work.
- [ ] Order: problem → **general RQs** → literature/state of the art (alternatives incl. knowledge graph, dictionary, LLM-only, grammar-constrained decoding) → *why* the hybrid follows → **specific RQs** → solution. LLM and ontologies must not appear from nowhere.
- [ ] Recap the RQs again at the end, each with its answer.
- [ ] One **architecture slide** (software architecture, not a flow chart) colouring inherited (Ludyga: grammar, ANTLR, 198 sentences) / modified (grammar) / new (stages, config) / external (LLMs, ontologies, Whisper). Show it *before* the pipeline.
- [ ] Pipeline overview only; all seven stage slides as **backup**.
- [ ] One **running example** carried through every stage with its output (use `set medication aspirin 100 mg`; show `diagnosis hemodialysis` as the safety catch — not `hemolysis`, see §9.3).
- [ ] Explainability slide: what the system can explain (decision trace: extracted intent, grammar result, ontology ID + source, type rule, reason) and what it cannot (LLM internals).
- [ ] State that the LLM also decides valid/invalid for non-clinical commands.
- [ ] Name the LLMs used and why (RQ4 result).
- [ ] Slide 26: make the responsibility split general (LLM / grammar / knowledge source / type rule), medical case only as example; slide 29: add a diagram.
- [ ] Requirements slide: each requirement with its metric and the evidence (table §3); include performance/latency.
- [ ] Evaluation slides: setup, results, latency, limitations.
- [ ] Slow down: one idea per slide, pause on figures; rehearse with a timer.

**Report**
- [ ] Introduction: general RQs first; specific RQs when the solution is introduced.
- [ ] Literature review chapter (missing at midterm): grammar-based interfaces, intent/slot models, LLM semantic parsing, constrained decoding, neuro-symbolic, ontology/KG grounding, explainability. Verify every citation from source before use; the only verified references today are the seven in the task description.
- [ ] Contribution table (§1) in the design chapter.
- [ ] Requirements chapter with metrics for the non-functional requirements (Lidia's question).
- [ ] Stage-order and alternative-knowledge-source discussion (§8) with the data-dependency argument.
- [ ] Explainability section with the explicit limitation.
- [ ] Evaluation chapter from `evaluation/reports/` only; threats to validity (development-set results, single domain, typed text not speech, LLM non-determinism, remote latency).
- [ ] Conclusion and future work.

---

## 13. Evaluation chapter: what goes in, and where it comes from

Every table and figure is generated from `runs/` (via `summary.json` / `overview_*.csv`), nothing is typed by hand.

| Thesis table / figure | Content | Source |
|---|---|---|
| Table: setup | dataset size and categories, models, repetitions, endpoint, commit, hardware | `meta.json`, `data/annotated_commands.json` |
| Table: baselines (E1) | B0 Ludyga vs hybrid vs LLM-only (B2): valid recall, valid → clarification, false rejection, false acceptance, precision, F1, strict/lenient accuracy; mean ± SD over 3 runs | `summary.json` (`hybrid`, `llm_only_B2`), B0 run |
| Table: per category | valid recall / rejection per command category, B0 vs hybrid | `summary.json` → `per_category` |
| Table: what each stage catches (E2) | first rejecting stage × gold valid/invalid | `summary.json` → `first_rejecting_stage` |
| Table: extraction quality (E4, RQ1) | action accuracy, entity accuracy, end-signal P/R/F1 per model | `summary.json` (entity/end-signal need `gold_labels.json`) |
| Table: entity resolution (E3, RQ2/RQ3) | resolution/type accuracy, coverage, incorrect acceptance | separate gold entity set (to be built) |
| Table + figure: LLM comparison (E4, RQ4) | accuracy and latency per model; accuracy-vs-latency scatter | `overview_*.csv` |
| Table + figure: latency (E5) | median/p95/mean per stage and end to end; cold-start build time | `summary.json` → `latency`, `meta.json` → `cold_start_…` |
| Table: explainability (E6a) | reason, explanation-block and provenance coverage; optional human check | `summary.json` → `explainability` |
| Table: requirements (§3) | each FR/NFR with its measured evidence | filled from the tables above |
| Table: extensibility (E6c) | files / LOC changed to add one action, regression unchanged | git diff of the demo |
| Section: limitations / threats | development-set results, one domain, typed text not speech, LLM non-determinism, remote latency, known issues in §9 | §9 of this plan |
| Section: discussion | stage-order argument, knowledge-graph / dictionary alternatives, constrained decoding | §8 of this plan + literature chapter |


---

## 14. Pipeline changes made during the evaluation

The first complete runs (their folders are no longer kept) exposed defects that appeared under every model. They were fixed once, and everything is reported as **before** (old runs) and **after** (new runs). Old run folders are untouched.

| # | Defect seen in the runs | Fix | Files |
|---|---|---|---|
| 1 | Valid entities containing grammar words are rejected by Stage 3 ("chest pain at rest", "stop frame": `at`, `on`, `frame` are lexer keywords, not plain words) | Stage 3 parses a **copy** of the entity in which keyword tokens are replaced by a neutral word; Stages 4–6 keep the original entity. Config key `stage3_entity_view: keywords_neutralised` on `diagnosis`, `start`, `stop`, `change_patient` (removed from all actions by 4 Nov 2026, see the update above) | `src/stage3_antlr_validate.py`, `domain_config.yaml` |
| 2 | Medication commands with frequency text ("2 puffs every 4 hours as needed", "once daily") fail the grammar; `o'clock` was normalised in the wrong order | Structure copy keeps `drug dose unit` per drug (and an optional `at <time>`), drops trailing instructions; missing dose/unit still fails; decimals collapsed; `o'clock` fix. `stage3_entity_view: medication_core` on `set_medication` | same |
| 3 | Bare "stop" ended in `needs_clarification` (empty entity treated as "not found") | `entity_optional: true` on `stop`; Stage 5 returns `skipped` when the entity is absent | `src/stage5_type_validate.py`, `domain_config.yaml` |
| 4 | DeepSeek and gpt-oss returned empty or cut-off JSON on 6.4 % / 1.8 % of the calls: the 500-token budget is consumed by their reasoning (`finish_reason=length`) | `extract(..., max_tokens)`; those two models are evaluated with 500 (as before) **and** 4000 tokens; both reported | `src/stage2_llm_extract.py`, `run_eval.py --max-tokens` |

Items 1–3 only change Stages 3–5, which are deterministic, so their effect on the old runs can be measured exactly without new LLM calls: `run_eval.py --replay <old run folders>` re-executes Stages 1, 3–6 on the stored Stage 2 outputs and writes `runs/<date>_*_full-replay_r*`. Replay latencies are not comparable (Stage 2 time is copied from the original run). Fresh complete runs under the fixed pipeline carry the tag `final`.

**Tried and removed (dataset-specific, to avoid overfitting to the development set):** (a) a curated list of "system targets" (Frame, Display, Animation) so that "stop frame" resolves; (b) excluding laboratory procedures from the NCIt index because of the single term "hemolysis"; (c) dropping trailing words from drug names ("albuterol inhaler" → "albuterol"). Each was motivated by individual failing items; the pipeline keeps only fixes 1–3, which repair design defects independent of any item. The consequences stay visible in the results and are reported as limitations: "start/stop hemolysis" is still accepted via the NCIt synonym collision, "stop frame" ends in a clarification (not found), "albuterol inhaler 2 puffs" ends in a clarification (drug not found).

Fairness note for the report: fixes 1–3 were also found while looking at development-set failures; the held-out set is the unbiased check, and old (before) and new (after) results are reported side by side.

## 15. Gold labels (what they are, how they are made, what they are used for)

- **What:** the human-verified correct answer for a command, as opposed to what the system produced. The dataset already has two gold fields (`valid`, `expected_action`). Missing: the correct **entity** per valid command, and whether a dictation command contains an **end signal**.
- **Used for:** entity accuracy and end-signal precision/recall/F1 (slide 32 metrics; FR intent extraction). Without them only action-level and accept/reject metrics can be computed.
- **How made:** `scripts/make_gold_labels.py draft` writes `dataset/gold_labels_worksheet.csv` (92 valid commands that carry an entity; entity-free commands are left out) with empty `entity` / `end_signal` columns that a human fills (no model output is shown, so the labels stay independent of the system) (`-` = correctly no entity); `build` turns the confirmed columns into `dataset/gold_labels.json`, which `run_eval.py` joins on the item id. Nothing is gold until a human has checked it. A second person should check a sample (agreement reported).
