# Pipeline Evolution — Baseline vs Enhanced

> Master Thesis · TU Dresden · Software Technology  
> *Improving Intent Recognition in Grammar-Based Voice Interfaces*

The interactive diagram at `pipeline_evolution.html` shows which parts of the pipeline are inherited from the Ludyga baseline, which were changed, and which are completely new additions introduced in this thesis.

---

## What the diagram shows

Ludyga's original system was a two-stage pipeline: basic text normalisation followed by ANTLR grammar parsing that did both action identification and structural validation in one step. It achieved 100% precision on invalid commands but only 50% recall on valid ones — the grammar was too strict to handle anything spoken informally.

The enhanced pipeline keeps ANTLR as the structural authority but splits its responsibilities. A new LLM extraction stage (Stage 2) sits between preprocessing and grammar validation, handling the parts that a lexer can't: filler words, synonym variation, and natural phrasing. ANTLR (Stage 3) then validates the LLM's output against the same grammar rules — entity slot format, command type, structural constraints — and remains the gatekeeping step it always was. Nothing passes Stage 3 if the grammar says no.

Stages 4 and 5 are entirely new. Stage 4 resolves the extracted entity against real medical ontologies (DOID, HP, NCIt Thesaurus, RxNorm) and flags type mismatches — for example, when someone says "diagnosis hemodialysis" and hemodialysis turns out to be a procedure, not a disease. Stage 5 enforces the semantic constraint for each action and generates a human-readable clarification message so the backend knows exactly what was wrong and why.

Stage 6 (Intent JSON output) is still in progress. It will assemble everything — action, canonical entity, semantic type, urgency flag from Stage 1 — into the final structured payload the backend consumes.

---

## Colour coding

| Colour | Meaning |
|--------|---------|
| Slate | Original Ludyga stage, unchanged in role |
| Amber | Stage existed before but was updated or repurposed |
| Teal | New stage added in this thesis |
| Grey dashed | Pending implementation (Stage 6) |

---

## Result

Across 243 test commands, the enhanced pipeline raises binary accuracy from 75.3% to 97.5%, valid recall from 50.0% to 98.5%, and maintains invalid precision at 96.5% (vs. Ludyga's 100% — the small drop is deliberate, trading off extreme conservatism for practical usability).
