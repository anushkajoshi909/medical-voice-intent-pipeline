#!/usr/bin/env python3
"""Interactive model selector for SCADS.AI.
Lists available chat models, user picks by number.
Writes chosen model to .selected_model so other scripts pick it up.
"""
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILE = ROOT / ".selected_model"

# Models that do text generation (skip embedding, image, TTS, STT)
SKIP_KEYWORDS = ["Embedding", "reranker", "stable-diffusion", "FLUX", "Kokoro", "tts", "whisper", "stt"]


def get_client():
    key = (Path.home() / ".scadsai-api-key").read_text().splitlines()[0].strip()
    return OpenAI(base_url="https://llm.scads.ai/v1", api_key=key)


def list_chat_models(client):
    all_models = [m.id for m in client.models.list().data]
    return [m for m in all_models
            if not any(kw.lower() in m.lower() for kw in SKIP_KEYWORDS)]


def main():
    print("Connecting to SCADS.AI...")
    client = get_client()
    models = list_chat_models(client)

    print(f"\nAvailable chat models ({len(models)}):\n")
    for i, m in enumerate(models):
        current = "  <-- current" if MODEL_FILE.exists() and MODEL_FILE.read_text().strip() == m else ""
        print(f"  [{i+1}] {m}{current}")

    print()
    while True:
        try:
            choice = input(f"Select model [1-{len(models)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                selected = models[idx]
                break
            print(f"  Enter a number between 1 and {len(models)}.")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            return

    MODEL_FILE.write_text(selected)
    print(f"\nSelected: {selected}")
    print(f"Saved to: {MODEL_FILE}")
    print("Stage 2 will use this model on next run.")


if __name__ == "__main__":
    main()
