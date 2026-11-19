#!/usr/bin/env python3
"""Stage 6: Intent JSON Assembly

Takes the results of Stages 1-5 and assembles the final structured payload
handed to the backend / Stage 7 confirmation UI.

Statuses produced:
  rejected              — Stage 1 format rejection, Stage 2 UNKNOWN, or
                           Stage 3 structural failure. For Stage 3 failures
                           where the action/entity were understood (just
                           structurally incomplete, e.g. medication missing
                           dose/unit), includes an actionable
                           clarification_message built from the action's
                           structure_hint in domain_config.yaml — but this
                           does NOT go through Stage 7's confirm/reject
                           dialogue, since nothing concrete was resolved to
                           confirm or deny (see docs/findings.md Finding 3).
  accepted              — Stage 5 status "accepted" or "skipped", end_signal
                           true (or action has no end-of-dictation concept)
  accepted_partial      — free-text action (set_note, send_message) still
                           open (end_signal=false); nothing to confirm yet
  needs_clarification   — Stage 5 status "type_mismatch" or "entity_not_found";
                           shown to the user with a request for a corrected
                           command, never confirmed (Stage 7)
  inactive              — not produced here: run_pipeline.run_command() returns
                           it when a new command has no trigger phrase

No domain knowledge is hardcoded here: allowed_slot_types, semantic types,
and action names all come from the Stage 2-5 results, which are themselves
driven by domain_config.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_FREE_TEXT_TYPES = {"free_text"}


@dataclass
class IntentResult:
    status: str                    # rejected | accepted | accepted_partial | needs_clarification (| inactive, see run_pipeline)
    payload: dict = field(default_factory=dict)


def _drug_slot_list(drug_list) -> list[dict]:
    slots = []
    for d in drug_list:
        dose_value, _, dose_unit = d.dose.partition(" ")
        slots.append({
            "raw":           d.raw_segment,
            "drug_label":    d.canonical,
            "rxcui":         d.ontology_id,
            "dose":          dose_value or None,
            "dose_unit":     dose_unit or None,
            "semantic_type": (d.semantic_type or "medication") if d.found else None,
            "found":         d.found,
            "match_type":    d.match_method,
        })
    return slots


def _entity_slot(stage4, semantic_type: str | None) -> dict:
    return {
        "raw_entity":    stage4.raw_entity,
        "canonical":     stage4.canonical,
        "semantic_type": semantic_type,
        "ontology_id":   stage4.ontology_id,
        "match_method":  stage4.match_method,
        "score":         stage4.score,
        "source":        stage4.source,
    }


def _intent_name(action: str, type_hint: str | None) -> str:
    base = action.upper()
    return f"{base}_{type_hint.upper()}" if type_hint else base


def _llm_explanation(stage1, stage2) -> str:
    parts = [f"Action {stage2.action!r} extracted with confidence {stage2.confidence}."]
    if stage1.abbreviations_expanded:
        parts.append(f"Abbreviations expanded: {stage1.abbreviations_expanded}.")
    if stage2.end_signal:
        parts.append("End-of-dictation signal detected.")
    return " ".join(parts)


def _grammar_explanation(stage3) -> str:
    if stage3.valid:
        return f"Frame valid. command_type={stage3.command_type}."
    return f"Frame invalid: {stage3.reason}."


def _ontology_explanation(stage4, action_info: dict) -> str:
    if action_info.get("ontology_index") is None:
        if action_info.get("passthrough_type"):
            return f"Free-text/passthrough slot; assigned semantic_type={action_info['passthrough_type']!r} without lookup."
        return "System command with no entity slot; no ontology lookup performed."
    if stage4.drug_list:
        found = sum(1 for d in stage4.drug_list if d.found)
        return f"{found}/{len(stage4.drug_list)} drug(s) confirmed via ontology lookup."
    if stage4.found:
        return (f"{stage4.raw_entity!r} matched {stage4.source} concept "
                f"{stage4.ontology_id} via {stage4.match_method} at score {stage4.score:.2f}.")
    if stage4.clarification_type:
        return (f"{stage4.raw_entity!r} was found as a {stage4.clarification_type!r} "
                f"({stage4.clarification_canonical!r}) in a different ontology index, "
                f"not in the index searched for this action.")
    return f"{stage4.raw_entity!r} was not found in any configured ontology index."


def _structure_clarification(stage2, stage3, action_info: dict) -> str | None:
    """Turn a Stage 3 structural failure into an actionable message, when
    there's enough known (action + entity) to say something specific.

    PARSE_ERROR: the action/entity were understood but the command is
    structurally incomplete (e.g. medication missing dose/unit) — uses the
    action's structure_hint from domain_config.yaml, so the wording stays
    config-driven rather than hardcoded per action here.

    UNEXPECTED_ENTITY: the action takes no entity at all, but one was
    extracted — this needs no per-action hint, it's a generic structural
    fact about the action regardless of domain.

    Other reasons (e.g. INVALID_ACTION) return None — Stage 2 already
    filters those out before this is ever reached, but it's a fallback
    in case stage3.reason doesn't match a known pattern.
    """
    label = stage2.action.replace("_", " ")

    if stage3.reason.startswith("PARSE_ERROR"):
        hint = action_info.get("structure_hint")
        if not hint:
            return None
        return (
            f"I understood you want to {label} '{stage2.entity}', but the command "
            f"is structurally incomplete: {hint}. Please repeat with the missing detail."
        )

    if stage3.reason == "UNEXPECTED_ENTITY":
        return (
            f"The '{label}' command doesn't take any additional details. "
            f"Please repeat it on its own, without extra content."
        )

    return None


def _compound_clarification(original_text, stage1, stage2, stage3, stage4, stage5,
                            action, action_info, urgency) -> IntentResult:
    """Clarification for a compound medication: names the segments that are not
    found or have a type that is not allowed, and keeps the per-drug results
    (resolved drugs included) without guessing a replacement."""
    allowed = stage5.allowed_types
    label = action.replace("_", " ")
    missing = [d.raw_segment for d in stage4.drug_list if not d.found]
    wrong = [d.raw_segment for d in stage4.drug_list
             if d.found and d.semantic_type not in allowed]
    resolved = [d.canonical for d in stage4.drug_list
                if d.found and d.semantic_type in allowed]
    if missing:
        problem = (f"I couldn't find {', '.join(repr(m) for m in missing)} "
                   f"in the reference data for '{label}'.")
    else:
        problem = (f"{', '.join(repr(w) for w in wrong)} is not a "
                   f"{'/'.join(allowed) or 'compatible type'}.")
    known = f" Recognised: {', '.join(resolved)}." if resolved else ""
    payload = {
        "status": "needs_clarification",
        "reason": stage5.status,
        "intent_attempted": _intent_name(action, allowed[0] if allowed else None),
        "action": action,
        "original_command": original_text,
        "raw_entity": stage4.raw_entity,
        "expected_semantic_types": allowed,
        "urgency": urgency,
        "drugs": _drug_slot_list(stage4.drug_list),
        "unresolved_segments": missing + wrong,
        "explanation": {
            "llm_extraction":      _llm_explanation(stage1, stage2),
            "grammar_validation":  _grammar_explanation(stage3),
            "ontology_evidence":   _ontology_explanation(stage4, action_info),
            "semantic_validation": stage5.reason,
        },
        "clarification_message": f"{problem}{known} Please say the corrected command.",
    }
    return IntentResult("needs_clarification", payload)


def assemble(original_text: str, stage1, stage2, stage3, stage4, stage5, action_config: dict) -> IntentResult:
    """Assemble the Stage 6 intent payload.

    stage1..stage5 are the dataclass results from the corresponding modules.
    action_config is domain_loader's per-action config dict (for slot naming).
    """
    action = stage2.action
    action_info = action_config.get(action, {})

    # ── Rejections (never reached Stage 5) ──────────────────────────────────
    if stage1.format_invalid:
        return IntentResult("rejected", {
            "status": "rejected",
            "reason": "non_speech_input",
            "original_command": original_text,
            "explanation": {"stage1": "Input rejected as non-speech (JSON/code/inaudible marker)."},
        })

    if action == "UNKNOWN" or not stage2.valid_action:
        return IntentResult("rejected", {
            "status": "rejected",
            "reason": "unrecognized_command",
            "original_command": original_text,
            "explanation": {"llm_extraction": "No valid action could be mapped from the input."},
        })

    if not stage3.valid:
        payload = {
            "status": "rejected",
            "reason": "invalid_structure",
            "action": action,
            "original_command": original_text,
            "explanation": {
                "llm_extraction":     _llm_explanation(stage1, stage2),
                "grammar_validation": _grammar_explanation(stage3),
            },
        }
        clarification = _structure_clarification(stage2, stage3, action_info)
        if clarification:
            payload["clarification_message"] = clarification
        return IntentResult("rejected", payload)

    urgency = stage1.urgency

    # ── Needs clarification (Stage 5 type_mismatch / entity_not_found) ─────
    if stage5.status in ("type_mismatch", "entity_not_found"):
        if stage4.drug_list:
            return _compound_clarification(original_text, stage1, stage2, stage3, stage4,
                                           stage5, action, action_info, urgency)
        if stage5.status == "type_mismatch":
            found_type  = stage4.clarification_type or stage5.semantic_type
            found_label = stage4.clarification_canonical or stage4.canonical or stage4.raw_entity
            clarification_message = (
                f"I heard '{stage4.raw_entity}' and recognised it as a {found_type}, "
                f"not a {'/'.join(stage5.allowed_types) or 'compatible type'}. "
                f"Please say the corrected command."
            )
            resolved_entity = {
                "matched_label":  found_label,
                "concept_id":     stage4.ontology_id,
                "semantic_type":  found_type,
                "match_type":     stage4.match_method if stage4.found else "exact",
            }
        else:
            clarification_message = (
                f"I couldn't find '{stage4.raw_entity}' in the reference data for '{action}'. "
                f"The command was understood, but the entity could not be grounded. "
                f"Please say the corrected command."
            )
            resolved_entity = None

        payload = {
            "status": "needs_clarification",
            "reason": stage5.status,
            "intent_attempted": _intent_name(action, stage5.allowed_types[0] if stage5.allowed_types else None),
            "action": action,
            "original_command": original_text,
            "raw_entity": stage4.raw_entity,
            "expected_semantic_types": stage5.allowed_types,
            "urgency": urgency,
            "explanation": {
                "llm_extraction":      _llm_explanation(stage1, stage2),
                "grammar_validation":  _grammar_explanation(stage3),
                "ontology_evidence":   _ontology_explanation(stage4, action_info),
                "semantic_validation": stage5.reason,
            },
            "clarification_message": clarification_message,
        }
        if resolved_entity is not None:
            payload["resolved_entity"] = resolved_entity
        return IntentResult("needs_clarification", payload)

    # ── Accepted / accepted_partial (Stage 5 accepted or skipped) ───────────
    passthrough_type = action_info.get("passthrough_type")
    is_free_text_open = (passthrough_type in _FREE_TEXT_TYPES) and not stage2.end_signal

    if is_free_text_open:
        payload = {
            "status": "accepted_partial",
            "intent": f"{action.upper()}_OPEN",
            "action": action,
            "original_command": original_text,
            "urgency": urgency,
            "slots": {
                "free_text": {
                    "content":    stage2.entity,
                    "end_signal": stage2.end_signal,
                    "note_state": "open -- awaiting end-of-dictation signal",
                }
            },
            "explanation": {
                "llm_extraction":      _llm_explanation(stage1, stage2),
                "grammar_validation":  _grammar_explanation(stage3),
                "ontology_evidence":   "Free-text slot; no ontology lookup.",
                "semantic_validation": stage5.reason,
            },
        }
        return IntentResult("accepted_partial", payload)

    slots: dict = {}
    if stage5.status != "skipped":
        semantic_type = stage5.semantic_type
        slot_key = semantic_type or action
        if stage4.drug_list:
            slots[slot_key] = _drug_slot_list(stage4.drug_list)
        elif semantic_type in _FREE_TEXT_TYPES or semantic_type == "patient_identifier":
            slots[slot_key] = {"content": stage2.entity, "recipient": stage2.recipient or None}
        else:
            slots[slot_key] = _entity_slot(stage4, semantic_type)

    payload = {
        "status": "accepted",
        "intent": _intent_name(action, stage5.semantic_type),
        "action": action,
        "original_command": original_text,
        "urgency": urgency,
        "slots": slots,
        "explanation": {
            "llm_extraction":      _llm_explanation(stage1, stage2),
            "grammar_validation":  _grammar_explanation(stage3),
            "ontology_evidence":   _ontology_explanation(stage4, action_info),
            "semantic_validation": stage5.reason,
        },
    }
    return IntentResult("accepted", payload)
