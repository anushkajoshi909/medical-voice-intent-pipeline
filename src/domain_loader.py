#!/usr/bin/env python3
"""Domain Loader — single source of truth wiring.

Reads:
  1. MedicalSmartGlassesParser.g4  → extracts command type rule names
  2. domain_config.yaml            → descriptions, slot types, rules, examples,
                                     grammar_command_type per action
  3. llm_system_prompt.txt         → generic template with {placeholders}

Produces:
  - system_prompt : filled prompt string for Stage 2 LLM call
  - action_config : dict of action → {description, allowed_slot_types, ...}
  - trigger_phrase: wake word
  - valid_actions : set of valid action names

Validation enforced at startup:
  Every action in domain_config.yaml must declare a grammar_command_type that
  exists as a rule in MedicalSmartGlassesParser.g4.
  → If grammar changes, domain_config MUST be updated. If it is not, startup fails.
"""
import re
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── 1. Extract command type rule names from parser grammar ────────────────────

def _load_grammar_command_types(parser_path: Path) -> set[str]:
    """Parse the ANTLR parser .g4 file to extract top-level command type rules.

    Reads the `command` rule's alternatives — these are the routing type names
    used by the backend (glasses_command, request_data, diagnosis, …).
    """
    text = parser_path.read_text()

    # Find the `command` rule body
    m = re.search(r"command\s*\n?\s*:(.+?);\s*\n", text, re.DOTALL)
    if not m:
        # Fallback: extract all parser rule names (lowercase identifiers)
        return set(re.findall(r'^([a-z][a-z0-9_]*)\s*:', text, re.MULTILINE))

    # Extract alternative names from the command rule
    body = m.group(1)
    types = re.findall(r'\b([a-z][a-z0-9_]+)\b', body)
    # Filter out parser keywords
    GRAMMAR_KW = {"eof", "trigger"}
    return {t for t in types if t not in GRAMMAR_KW}


# ── 2. Build filled prompt ────────────────────────────────────────────────────

def _build_action_list(cfg: dict) -> str:
    lines = []
    for name, info in cfg["actions"].items():
        lines.append(f"  {name}: {info['description']}")
    return "\n".join(lines)


def _build_domain_rules(cfg: dict) -> str:
    rules = cfg.get("domain_rules", [])
    return "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))


def _build_few_shot_examples(cfg: dict) -> str:
    examples = cfg.get("few_shot_examples", [])
    lines = []
    for ex in examples:
        lines.append(f'Input:  "{ex["input"]}"')
        lines.append(f"Output: {ex['output']}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Public API ────────────────────────────────────────────────────────────────

def load() -> dict:
    """Load and validate domain configuration.

    Returns a dict with keys:
      system_prompt, action_config, trigger_phrase, valid_actions,
      grammar_command_types
    """
    parser_path = ROOT / "MedicalSmartGlassesParser.g4"
    config_path = ROOT / "domain_config.yaml"
    prompt_path = ROOT / "llm_system_prompt.txt"

    grammar_types = _load_grammar_command_types(parser_path)
    cfg            = yaml.safe_load(config_path.read_text())

    # Validate: every action's grammar_command_type must exist in parser
    for action, info in cfg["actions"].items():
        gtype = info.get("grammar_command_type")
        if gtype is None:
            raise ValueError(
                f"Action '{action}' in domain_config.yaml has no grammar_command_type. "
                f"Add grammar_command_type matching one of: {sorted(grammar_types)}"
            )
        if gtype not in grammar_types:
            raise ValueError(
                f"Action '{action}' declares grammar_command_type='{gtype}' "
                f"but '{gtype}' is not a rule in MedicalSmartGlassesParser.g4.\n"
                f"Known grammar rules: {sorted(grammar_types)}"
            )

    # Validate: every action needs the text template Stage 3 feeds to the grammar
    for action, info in cfg["actions"].items():
        if not info.get("stage3_template"):
            raise ValueError(
                f"Action '{action}' in domain_config.yaml has no stage3_template "
                f"(text the grammar parses, e.g. \"{{trigger}} show {{entity}}\")."
            )

    template      = prompt_path.read_text()
    system_prompt = (
        template
        .replace("{DOMAIN_DESCRIPTION}", cfg["domain_description"])
        .replace("{ACTION_LIST}",         _build_action_list(cfg))
        .replace("{DOMAIN_RULES}",        _build_domain_rules(cfg))
        .replace("{FEW_SHOT_EXAMPLES}",   _build_few_shot_examples(cfg))
    )

    return {
        "system_prompt":       system_prompt,
        "action_config":       cfg["actions"],
        "trigger_phrase":      cfg["trigger_phrase"],
        "valid_actions":       set(cfg["actions"].keys()),
        "grammar_command_types": grammar_types,
    }


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    domain = load()
    print("Grammar command types:", sorted(domain["grammar_command_types"]))
    print("Config actions       :", sorted(domain["valid_actions"]))
    print("\n--- System prompt ---")
    print(domain["system_prompt"])
