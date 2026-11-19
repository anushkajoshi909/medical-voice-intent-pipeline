#!/usr/bin/env python3
"""Whisper ASR integration via the SCADS.AI OpenAI-compatible API.

Upstream component: synthesize speech for a command and transcribe it
back with Whisper, simulating the ASR stage that sits in front of the
intent-recognition grammar. Demonstrates ASR-induced errors such as the
hemodialysis/hemolysis confusion described in the pipeline design.

Note: SCADS.AI's hosted TTS endpoints (tts-1-hd, Kokoro-82M) returned
empty/error responses at the time of writing, so speech synthesis uses
macOS `say` + `afconvert` locally instead. Only the transcription step
(transcribe()) depends on the SCADS.AI Whisper model — that is the actual
upstream ASR component integrated into the pipeline.
"""
import subprocess
from pathlib import Path
from openai import OpenAI

WHISPER_MODEL = "alias-stt"
AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "audio"


def get_client() -> OpenAI:
    key_path = Path.home() / ".scadsai-api-key"
    api_key = key_path.read_text().splitlines()[0].strip()
    return OpenAI(base_url="https://llm.scads.ai/v1", api_key=api_key)


def synthesize(text: str, out_path: Path, voice: str = "Samantha") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    aiff_path = out_path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff_path), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff_path), str(out_path)], check=True)
    aiff_path.unlink()
    return out_path


def transcribe(client: OpenAI, audio_path: Path, language: str = "en") -> str:
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=WHISPER_MODEL, file=f, language=language
        )
    return result.text


def asr_roundtrip(client: OpenAI, text: str, name: str, voice: str = "Samantha") -> str:
    audio_path = AUDIO_DIR / f"{name}.wav"
    synthesize(text, audio_path, voice=voice)
    return transcribe(client, audio_path)


if __name__ == "__main__":
    client = get_client()
    demo_phrases = [
        ("ok glasses start hemodialysis", "start_hemodialysis"),
        ("ok glasses diagnosis hemolysis", "diagnosis_hemolysis"),
        ("ok glasses illustrate the patient's medical history", "illustrate_history"),
        ("ok glasses stop frame", "stop_frame"),
    ]
    for text, name in demo_phrases:
        transcript = asr_roundtrip(client, text, name)
        match = "MATCH" if transcript.strip().lower().rstrip(".") == text.lower() else "DIFFERS"
        print(f"[{match}] input:  {text}")
        print(f"        whisper: {transcript.strip()}\n")
