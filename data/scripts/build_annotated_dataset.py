#!/usr/bin/env python3
"""Build data/annotated_commands.json — commands_200 + extended, with expected_action labels.

Sources for expected_action:
  1. Grammar-accepted commands: derived from Ludyga's Evaluation-data.md grammar output
  2. Grammar-rejected valid commands: manually annotated from command text semantics
  3. Invalid commands: expected_action = null (correct behaviour is rejection)
  4. Extended test cases: already have expected_action in extended_test_cases.json
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ── 1. Grammar type → our action taxonomy ────────────────────────────────────
GRAMMAR_TYPE_MAP = {
    ("GLASSES_COMMAND", "STOP_FRAME"):      "stop",
    ("GLASSES_COMMAND", "CONTINUE_FRAME"):  "continue",
    ("GLASSES_COMMAND", "TURN_ON"):         "turn_on",
    ("GLASSES_COMMAND", "TURN_OFF"):        "turn_off",
    ("GLASSES_COMMAND", "START_EMERGENCY"): "emergency",
    ("GLASSES_COMMAND", "END_EMERGENCY"):   "leave_emergency",
    ("GLASSES_COMMAND", "ACCEPT_EMERGENCY"):"accept_emergency",
    ("GLASSES_COMMAND", "LEAVE_EMERGENCY"): "leave_emergency",
    ("GLASSES_COMMAND", "SHOW_MESSAGES"):   "show",
    ("REQUEST_DATA",    None):              "show",   # any entity
    ("PROTOCOL",        None):              "set_note",
    ("REQUEST_PATIENT", None):              "change_patient",
    ("MESSAGE",         None):              "send_message",
}

# ── 2. Parse Evaluation-data.md → {command_text: grammar_output} ─────────────
def parse_grammar_outputs():
    text = (ROOT / "baseline_ludyga_wiki" / "Evaluation-data.md").read_text()
    blocks = re.split(r"Input: ", text)[1:]
    out = {}
    for block in blocks:
        lines = block.strip().split("\n")
        cmd = lines[0].strip()
        m = re.search(r"\{[^{}]+\}", block, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group())
                if "type" in obj:
                    out[cmd] = obj
            except Exception:
                pass
    return out

def grammar_to_action(obj: dict) -> str | None:
    t = obj.get("type", "")
    c = obj.get("content", "")
    key_exact = (t, c)
    if key_exact in GRAMMAR_TYPE_MAP:
        return GRAMMAR_TYPE_MAP[key_exact]
    # Wildcard match on type alone
    key_wild = (t, None)
    return GRAMMAR_TYPE_MAP.get(key_wild)

# ── 3. Manual annotations for grammar-rejected valid commands ─────────────────
# These are valid commands whose intent is clear from text but grammar rejected them.
MANUAL_ANNOTATION = {
    # stop synonyms
    "Freeze frame":                "stop",
    "Halt animation":              "stop",
    "Stop now frame":              "stop",
    "Terminate the emergency":     "leave_emergency",
    "Cancel emergency mode":       "leave_emergency",
    "Stop emergency":              "leave_emergency",
    # continue synonyms
    "Continue frame, start now":   "continue",
    # turn_on synonyms
    "Activate display":            "turn_on",
    "Display on":                  "turn_on",
    "Turn on the display":         "turn_on",
    "please turn on":              "turn_on",
    # turn_off synonyms
    "disable the screen":          "turn_off",
    "turn it off":                 "turn_off",
    "Deactivate emergency mode":   "leave_emergency",
    "Disable emergency mode":      "leave_emergency",
    # emergency synonyms
    "start emergency":             "emergency",
    "finish emergency":            "leave_emergency",
    "I need help! Start emergency":"emergency",
    "Begin emergency":             "emergency",
    "Trigger emergency situation": "emergency",
    "Code red, initiate immediately": "emergency",
    # accept_emergency synonyms
    "accept catastrophe accident mode": "accept_emergency",
    "accept emergency mode":       "accept_emergency",
    # leave_emergency synonyms
    "Leave crisis mode":           "leave_emergency",
    "leave the emergency mode now":"leave_emergency",
    "leave emergency":             "leave_emergency",
    # show synonyms
    "Illustrate the insights":     "show",
    "Illustrate the patient's medical history": "show",
    "Present the blood test findings for the last month": "show",
    "Bring up the medication history of the past week":   "show",
    "Exhibit the surgical procedure details from the last three days": "show",
    "Demonstrate the pathology reports for the last 24 hours":        "show",
    "Reveal the immunization history of the last 10 days":            "show",
    "Bring up the chemotherapy regimen recorded in the past six hours": "show",
    "Show the genetic testing results over the past 48 hours":          "show",
    "Display diagnostic information": "show",
    "Display blood pressure":      "show",
    "Show messages view":          "show",
    "Display messages":            "show",
    "Show me the messages":        "show",
    # set_note synonyms
    "Note: Upon examination, there was tenderness in the lower abdomen and mild guarding. No signs of peritonitis. Finish note": "set_note",
    "Record note: Patient's blood pressure was elevated at 160/90 mmHg during the visit. Finish note": "set_note",
    "Add note: Patient reports experiencing shortness of breath and chest pain. Finish note": "set_note",
    "Log note: Radiology report confirms the presence of a 3 cm mass in the right lung. Finish note": "set_note",
    # set_medication synonyms
    "Set medication regimen for patient John Doe: Begin with 10 milligrams of Lisinopril once daily, finish medication": "set_medication",
    "Set medication protocol: Commence treatment with 500 milligrams of Metformin twice daily, finish medication": "set_medication",
    "Start medication plan for Mrs. Smith: Begin with 20 milligrams of Atorvastatin every evening, finish medication": "set_medication",
    "Activate drug regimen for Mr. Johnson: Start with 50 micrograms of Levothyroxine in the morning": "set_medication",
    "Start medication aspirin 100 milligrams and clopidogrel 75 milligrams, finish medication": "set_medication",
    # send_message synonyms
    "Start a new message: Inform Mrs. Smith about her upcoming appointment. Finish the message and send it to Mrs. Smith": "send_message",
    "Start message: Remind Mr. Johnson to take his medication as prescribed. Finish and send it to Mr. Johnson": "send_message",
    "Start new message: Note that Ms. Rodriguez's test results are ready for review. Finish message and send it to Ms. Rodriguez": "send_message",
    "Begin message: Advise Mr. Patel to schedule a follow-up consultation. Complete the message and send it to Mr. Patel": "send_message",
    "Start new message: Notify Dr. Lee about the patient's allergic reaction. Finish message and send it to Dr. Lee": "send_message",
    "Start a new message: Document the change in Mrs. Thompson's medication dosage. Finish the message and send it to Mrs. Thompson": "send_message",
    "Start message: Update Mr. Nguyen on his upcoming surgery details. Finish and send it to Mr. Nguyen": "send_message",
    "Begin new message: Record Ms. Garcia's recent symptoms for review. Complete the message and send it to Ms. Garcia": "send_message",
    "Start message: Inform Dr. Patel about the patient's discharge instructions. Finish and send it to Dr. Patel": "send_message",
    "Start a new message: Notify Mrs. Kim about her laboratory test scheduling. Finish the message and send it to Mrs. Kim": "send_message",
    # turn_on (grammar accepted variant)
    "turn the display on":            "turn_on",
    # set_note (grammar accepted with full text)
    "Note: Upon examination, there was tenderness in the lower abdomen and a palpable mass": "set_note",
    "Record note: Patient's blood pressure was elevated at 160/90 mmHg during today's visit": "set_note",
    "Add note: Patient reports experiencing shortness of breath and chest pain upon exertion": "set_note",
    "Log note: Radiology report confirms the presence of a 3 cm mass in the left lung with possible metastasis, finish note": "set_note",
    # set_medication (grammar accepted with full text)
    "Set medication protocol: Commence treatment with 500 milligrams of Metformin twice a day, finish medication": "set_medication",
    "Set medication metformin 500 milligrams at 10 am, finish medication": "set_medication",
    "Set medication lisinopril 10 milligrams at 4 pm, finish medication": "set_medication",
    "Set medication levothyroxine 50 micrograms once daily and omeprazole 20 milligrams once daily, finish medication": "set_medication",
    "Set medication albuterol inhaler 2 puffs every 4 hours as needed, finish medication": "set_medication",
    "Set medication lisinopril 10 milligrams and atorvastatin 20 milligrams at 11 o'clock, finish medication": "set_medication",
    # change_patient synonyms
    "Switch patient to Mary Johnson": "change_patient",
    "Update patient to Sarah Davis":  "change_patient",
    "Switch patient to Mary Smith":   "change_patient",
    "Switch to messages":             "show",
    # start synonyms
    "start the emergency":            "start",
    "Emergency in the kitchen! Start emergency": "emergency",
}


def main():
    grammar_outputs = parse_grammar_outputs()
    commands = json.loads((ROOT / "data" / "commands_200.json").read_text())
    extended = json.loads((ROOT / "data" / "extended_test_cases.json").read_text())

    annotated = []
    missing = []

    for cmd in commands:
        entry = dict(cmd)
        text = cmd["text"]

        if not cmd["valid"]:
            entry["expected_action"] = None
            entry["action_source"] = "invalid"
        elif text in grammar_outputs:
            action = grammar_to_action(grammar_outputs[text])
            entry["expected_action"] = action
            entry["action_source"] = "grammar"
        elif text in MANUAL_ANNOTATION:
            entry["expected_action"] = MANUAL_ANNOTATION[text]
            entry["action_source"] = "manual"
        else:
            entry["expected_action"] = None
            entry["action_source"] = "missing"
            missing.append(cmd["id"] + ": " + text[:60])

        annotated.append(entry)

    # Extended test cases already have expected_action
    for ext in extended:
        entry = dict(ext)
        entry["action_source"] = "manual"
        annotated.append(entry)

    out_path = ROOT / "data" / "annotated_commands.json"
    out_path.write_text(json.dumps(annotated, indent=2, ensure_ascii=False))

    total      = len(annotated)
    valid_ann  = sum(1 for e in annotated if e["valid"] and e.get("expected_action"))
    invalid_   = sum(1 for e in annotated if not e["valid"])
    from_gram  = sum(1 for e in annotated if e.get("action_source") == "grammar")
    from_man   = sum(1 for e in annotated if e.get("action_source") == "manual")

    print(f"Written: {out_path}")
    print(f"  Total entries  : {total}")
    print(f"  Valid annotated: {valid_ann}/98  (grammar={from_gram}, manual={from_man})")
    print(f"  Invalid (null) : {invalid_}")
    if missing:
        print(f"\n  Missing annotation ({len(missing)}):")
        for m in missing:
            print(f"    {m}")


if __name__ == "__main__":
    main()
