// MedicalSmartGlassesLexer.g4
// Extended from Ludyga (baseline_ludyga/MedicalSmartGlassesLexer.g4).
// LLM (Stage 2) normalises synonyms before this stage runs — grammar sees
// canonical tokens only. New addition: DIAGNOSIS command type.

lexer grammar MedicalSmartGlassesLexer;

TRIGGER: 'ok glasses ';

// glasses_command
STOP     : ('stop' | 'hold') ;
FRAME    : 'frame' ;
CONTINUE : 'continue' ;
TURN     : 'turn' ;
ON       : 'on' ;
OFF      : 'off' ;
DISPLAY  : 'display' ;
START    : 'start' ;
FINISH   : ('finish' | 'complete') ;
ACCEPT   : 'accept' ;
LEAVE    : 'leave' ;
MODE     : 'mode' ;
EMERGENCY: 'emergency' ;
SWITCH   : 'switch' ;
SHOW     : ('show' | 'present') ;
TO       : 'to' ;
MESSAGES : 'messages' ;

// request_data
OF       : 'of' ;
LAST     : 'last' ;
TIME_UNIT: (DAY | HOUR | MINUTE) S? ;
DAY      : 'day' ;
HOUR     : 'hour' ;
MINUTE   : 'minute' ;
S        : 's' ;

// protocol (set_note)
SET      : 'set' ;
NOTE     : 'note' ;
DOCUMENTATION: 'documentation' ;
TREATMENT: 'treatment' ;   // fixed second keyword of 'start treatment <entity>' (u22); only in the rebuilt command of Stage 3

// set_medication
MEDICATION: 'medication' ;
AND      : 'and' ;
AT       : 'at' ;
OCLOCK   : 'oclock' ;
PM       : 'pm' ;
AM       : 'am' ;

// request_patient
CHANGE   : 'change' ;
PATIENT  : 'patient' ;

// message
NEW      : 'new' ;
MESSAGE  : 'message' ;
SEND     : 'send' ;
IT       : 'it' ;

// diagnosis (new — not in Ludyga baseline)
DIAGNOSIS: 'diagnosis' ;

WS         : [ \t\r\n]+ -> skip ;
PLEASE     : 'please'   -> skip ;
THE        : 'the'      -> skip ;
A          : 'a'        -> skip ;
I_WANT_TO  : 'i want to' -> skip ;

INT  : [0-9]+ ;
WORD : [a-zA-Z]+ ;
