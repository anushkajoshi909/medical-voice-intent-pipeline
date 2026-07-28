// MedicalSmartGlassesLexer.g4 — Simplified validation grammar (canonical forms only).
// LLM normalises all synonyms before this stage runs.
// Used in Stage 3: ANTLR Frame Validation.

lexer grammar MedicalSmartGlassesLexer;

TRIGGER          : 'ok glasses' ;

// Canonical action tokens — one per action in domain_config.yaml
START            : 'start' ;
SHOW             : 'show' ;
DIAGNOSIS        : 'diagnosis' ;
SET_NOTE         : 'set_note' ;
SET_MED          : 'set_medication' ;
STOP             : 'stop' ;
TURN_ON          : 'turn_on' ;
TURN_OFF         : 'turn_off' ;
CONTINUE         : 'continue' ;
CHANGE_PT        : 'change_patient' ;
SEND_MSG         : 'send_message' ;
EMERGENCY        : 'emergency' ;
ACCEPT_EMERGENCY : 'accept_emergency' ;
LEAVE_EMERGENCY  : 'leave_emergency' ;

NUMBER           : [0-9]+ ;
WORD             : [a-zA-Z_]+ ('-' [a-zA-Z_]+)* ;
WS               : [ \t\r\n]+ -> skip ;
