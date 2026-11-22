#!/usr/bin/env python3
"""Push-to-talk recording for the --voice session (run_pipeline.py).

record_and_transcribe() records from the microphone until Enter and returns the
Whisper transcript (whisper_asr.py). voice_answer() lets a confirmation
question in voice mode use the same deadline as in text mode.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def record_and_transcribe(asr_client, ask_enter: bool = True) -> str:
    """Record from the microphone (Enter starts, Enter stops), save the audio to
    data/audio/mic_capture.wav and return the Whisper transcript."""
    # imported here: the audio packages are only needed in voice mode
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from whisper_asr import transcribe

    SAMPLE_RATE = 16000
    out_path = ROOT / "data" / "audio" / "mic_capture.wav"

    if ask_enter:
        input("Press Enter to start recording...")
    print("Recording... press Enter to stop.")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
    with stream:
        input()
    print("Stopped recording.")

    audio = np.concatenate(frames, axis=0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, audio, SAMPLE_RATE)

    print("Transcribing with Whisper...")
    text = transcribe(asr_client, out_path).strip()
    print(f"Transcript: {text!r}\n")
    return text


def voice_answer(read_answer, record):
    """Stage 7 read_response for push-to-talk voice mode.

    The confirmation deadline applies to pressing Enter (starting the recording):
    push-to-talk cannot hear silence, so not pressing Enter is the silence of
    voice mode. No Enter before the action's confirmation_timeout_sec -> None,
    and Stage 7 confirms automatically, as in text mode. Enter in time -> the
    reply is recorded and transcribed (the recording itself has no deadline).
    An explicit-reply action (timeout None) waits for Enter without limit.
    read_answer(timeout, prompt) is TerminalLineReader.read_answer; record() records
    and transcribes one reply."""
    def read_response(timeout):
        prompt = ("  Press Enter to answer by voice"
                  + (f" (no answer within {timeout:g}s: confirmed automatically)" if timeout else "")
                  + "... ")
        if read_answer(timeout, prompt) is None:
            print()
            return None
        return record()
    return read_response
