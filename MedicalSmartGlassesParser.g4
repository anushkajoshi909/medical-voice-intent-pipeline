// MedicalSmartGlassesParser.g4
// Extended from Ludyga (baseline_ludyga/MedicalSmartGlassesParser.g4).
// Adds: diagnosis (u21), start_treatment (u22; fixed keyword TREATMENT after START). u20 reverted to Ludyga original.
// Retains: command type hierarchy, medication structure enforcement.
//
// Stage 3 reconstructs a canonical string from LLM {action, entity} output
// and feeds it to this parser. If the string does not match any rule → invalid.

parser grammar MedicalSmartGlassesParser;
options { tokenVocab = MedicalSmartGlassesLexer; }

command
    : TRIGGER (glasses_command | request_data | protocol | treatment | stop_cmd
              | set_medication | request_patient | message | diagnosis) EOF
    ;

// ── Command types → backend routing ─────────────────────────────────────────
glasses_command : frame | switch_cmd | emergency | show_messages ;
request_data    : u13 | u14 ;
protocol        : u15 ;               // set_note (documentation)
treatment       : u22 ;               // start treatment/procedure
stop_cmd        : u01 ;
set_medication  : u16 | u17 ;
request_patient : u18 ;
message         : u20 ;
diagnosis       : u21 ;

// ── glasses_command ──────────────────────────────────────────────────────────
frame      : u02 ;
switch_cmd : u03 | u04 ;
emergency  : emergency_launch | emergency_acceptation ;
emergency_launch      : u09 | u10 ;
emergency_acceptation : u11 | u12 ;
show_messages : u19 ;

// ── Individual use cases ─────────────────────────────────────────────────────
u01 : STOP any_text? ;                           // stop [entity]
u02 : CONTINUE (FRAME)? ;                        // continue [frame]
u03 : TURN OFF (DISPLAY)? ;                      // turn off [display]
u04 : TURN ON (DISPLAY)? ;                       // turn on [display]
u09 : START EMERGENCY ;                          // start emergency
u10 : FINISH EMERGENCY ;                         // finish emergency
u11 : ACCEPT EMERGENCY (MODE)? ;                 // accept emergency [mode]
u12 : LEAVE EMERGENCY (MODE)? ;                  // leave emergency [mode]
u13 : SHOW data interval ;                       // show <data> of last N unit
u14 : SHOW data ;                                // show <data>
u15 : SET NOTE note FINISH NOTE                  // set note <text> finish note
    | START DOCUMENTATION note FINISH DOCUMENTATION ;
u16 : trig_medication MEDICATION medications FINISH MEDICATION ;   // dose required
u17 : trig_medication MEDICATION medications AT timestamp FINISH MEDICATION ;
u18 : CHANGE PATIENT TO patient_name ;           // change patient to <name>
u19 : trig_message MESSAGES ;                    // show messages / switch to messages
u20 : START (NEW)? MESSAGE note FINISH (NEW)? MESSAGE (AND)? SEND IT TO patient_name ; // compose and send to recipient
u21 : DIAGNOSIS any_text ;                       // diagnosis <entity>
u22 : START TREATMENT any_text ;                 // start treatment <entity>; the fixed keyword keeps it apart from the other START commands

// ── Sub-rules ─────────────────────────────────────────────────────────────────
data         : . (.)*? ;
interval     : OF LAST time ;
time         : INT TIME_UNIT ;
note         : . (.)*? ;
any_text     : . (.)*? ;                          // one or more tokens of any kind
trig_medication : SET | START ;
medications  : medication (AND medication)* ;
medication   : medicine INT unit
             | INT unit OF medicine ;
medicine     : ~INT (~(INT | AND))* ;
unit         : ~INT (~INT)? (~INT)? ;
timestamp    : INT (OCLOCK | PM | AM) ;
patient_name : WORD (WORD)? (WORD)? (WORD)? ;
trig_message : SWITCH TO | SHOW ;
