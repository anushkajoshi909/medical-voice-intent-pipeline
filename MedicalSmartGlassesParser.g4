// MedicalSmartGlassesParser.g4 — Frame validation rules.
// Validates structural correctness of LLM-extracted {action, entity}.
// Input to ANTLR is the reconstructed canonical form:
//   "ok glasses <action_name> <entity>"
// where action_name is already the canonical token from domain_config.yaml.

parser grammar MedicalSmartGlassesParser;
options { tokenVocab = MedicalSmartGlassesLexer; }

// Root — validates structural frame of LLM extraction
command
    : TRIGGER action_with_entity    EOF   # entityCommand
    | TRIGGER action_optional_entity EOF  # entityOptionalCommand
    | TRIGGER action_no_entity      EOF   # entityFreeCommand
    ;

// Actions that REQUIRE a non-empty entity
action_with_entity
    : START     entity
    | SHOW      entity
    | DIAGNOSIS entity
    | SET_NOTE  entity
    | SET_MED   entity
    | CHANGE_PT entity
    | SEND_MSG  entity
    ;

// Actions where entity is optional (bare or with entity both valid)
// STOP: "stop" (display/frame) OR "stop hemodialysis" (treatment)
action_optional_entity
    : TURN_ON  entity?
    | TURN_OFF entity?
    | CONTINUE entity?
    | STOP     entity?
    ;

// Actions where entity is forbidden
action_no_entity
    : EMERGENCY
    | ACCEPT_EMERGENCY
    | LEAVE_EMERGENCY
    ;

entity : (WORD | NUMBER)+ ;
