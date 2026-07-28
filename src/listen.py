#!/usr/bin/env python3
"""Push-to-talk CLI: record from the mic, transcribe with Whisper
(SCADS.AI), then run the transcript through Ludyga's grammar pipeline.

Usage:
    python src/listen.py

Press Enter to start recording, speak (include "ok glasses ..."),
press Enter again to stop.
"""
import sys
import logging
from pathlib import Path

import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "baseline_ludyga"))
logging.disable(logging.CRITICAL)

from src.whisper_asr import get_client, transcribe  # noqa: E402
from commandToJson import get_json  # noqa: E402

SAMPLE_RATE = 16000
OUT_PATH = ROOT / "data" / "audio" / "mic_capture.wav"


def record() -> Path:
    input("Press Enter to start recording...")
    print("Recording... press Enter to stop.")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
    with stream:
        input()
    print("Stopped recording.")

    import numpy as np
    audio = np.concatenate(frames, axis=0)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sf.write(OUT_PATH, audio, SAMPLE_RATE)
    return OUT_PATH


def main():
    client = get_client()
    audio_path = record()

    print("Transcribing with Whisper...")
    transcript = transcribe(client, audio_path)
    print(f"\nTranscript: {transcript.strip()}")

    print("\nRunning through Ludyga's grammar pipeline...")
    try:
        result = get_json(transcript)
    except Exception as e:
        result = {}
        print(f"(grammar raised {type(e).__name__}: {e})")

    import json
    print(json.dumps(result, indent=2) if result else "{}  (rejected: no trigger / grammar mismatch)")


if __name__ == "__main__":
    main()
