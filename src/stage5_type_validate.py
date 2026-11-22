"""Stage 5: Semantic Type Validation

Checks that the ontology-resolved semantic_type is permitted
by the action's allowed_slot_types (declared in domain_config.yaml).

Possible statuses:
  accepted          — semantic_type ∈ allowed_slot_types
  type_mismatch     — entity found in ontology but wrong type for this action
  entity_not_found  — Stage 4 found nothing; intent clear but entity unknown
  skipped           — system command with no entity slot (emergency, turn_on …)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from domain_loader import load as _domain


@dataclass
class Stage5Result:
    status: str                   # accepted | type_mismatch | entity_not_found | skipped
    semantic_type: str | None
    allowed_types: list = field(default_factory=list)
    reason: str = ""


# ── main entry point ─────────────────────────────────────────────────────────

def validate(action: str, stage4) -> Stage5Result:
    """
    action  — Stage 2 action name (e.g. 'diagnosis')
    stage4  — OntologyResult from stage4_ontology_lookup.lookup()
    """
    action_info = _domain()["action_config"].get(action, {})
    allowed     = action_info.get("allowed_slot_types", [])
    ont_index   = action_info.get("ontology_index")
    pass_type   = action_info.get("passthrough_type")

    # Action valid without an entity (e.g. bare "stop"): nothing to validate
    if action_info.get("entity_optional") and not (stage4.raw_entity or "").strip():
        return Stage5Result("skipped", None, allowed,
                            "optional entity absent — nothing to validate")

    # System commands (emergency, turn_on/off …): no entity slot, nothing to validate
    if ont_index is None and not pass_type:
        return Stage5Result("skipped", None, allowed,
                            "system command — no entity slot")

    # Passthrough actions (free_text, patient_identifier): type is always valid by design
    if pass_type:
        return Stage5Result("accepted", pass_type, allowed,
                            f"passthrough — semantic_type={pass_type!r} assigned without lookup")

    # Compound medication: every drug must be found and have an allowed type
    drugs = getattr(stage4, "drug_list", None)
    if drugs:
        missing = [d.raw_segment for d in drugs if not d.found]
        if missing:
            return Stage5Result(
                "entity_not_found", None, allowed,
                f"{len(missing)} of {len(drugs)} drug(s) not found: {missing}",
            )
        wrong = [d for d in drugs if d.semantic_type not in allowed]
        if wrong:
            return Stage5Result(
                "type_mismatch", wrong[0].semantic_type, allowed,
                f"{[d.raw_segment for d in wrong]} resolved as "
                f"{sorted({str(d.semantic_type) for d in wrong})} but {action!r} expects one of {allowed}",
            )
        return Stage5Result("accepted", drugs[0].semantic_type, allowed,
                            f"all {len(drugs)} drugs found; types ∈ allowed_slot_types for {action!r}")

    # Ontology lookup: entity not found in action's index
    if not stage4.found or stage4.match_method == "none":
        # Global clarification search found it in a different index → type mismatch
        if stage4.clarification_type:
            canonical = stage4.clarification_canonical or stage4.raw_entity
            return Stage5Result(
                "type_mismatch", stage4.clarification_type, allowed,
                f"'{canonical}' is a {stage4.clarification_type!r} — "
                f"{action!r} expects one of {allowed}",
            )
        # Genuinely unknown entity
        return Stage5Result(
            "entity_not_found", None, allowed,
            f"entity {stage4.raw_entity!r} not found in any ontology index",
        )

    # Ontology lookup: entity found in action's index — check type
    stype = stage4.semantic_type
    if stype in allowed:
        return Stage5Result("accepted", stype, allowed,
                            f"{stype!r} ∈ allowed_slot_types for {action!r}")

    # Found in action's own index but wrong type (edge case: multi-type index)
    canonical = stage4.canonical or stage4.raw_entity
    return Stage5Result(
        "type_mismatch", stype, allowed,
        f"'{canonical}' resolved as {stype!r} but {action!r} expects one of {allowed}",
    )
