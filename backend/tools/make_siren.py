"""Generates static/audio/ambulance-siren.wav - an original ambulance-style "wail" made by this script.

No third-party recording is used, so the file has no copyright restrictions for this project.
Run:  python tools/make_siren.py
"""
import wave
from pathlib import Path

import numpy as np

RATE = 22050
LOW, HIGH = 650.0, 1450.0   # Hz - typical ambulance wail range
CYCLE = 1.6                 # seconds for one up-and-down sweep
CYCLES = 2


def siren():
    t = np.arange(int(RATE * CYCLE * CYCLES)) / RATE
    # smooth up/down sweep (raised cosine), like a mechanical siren winding up and down
    sweep = (1 - np.cos(2 * np.pi * t / CYCLE)) / 2
    freq = LOW + (HIGH - LOW) * sweep
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    # a few odd harmonics give the bright "horn" timbre of a real siren speaker
    wave_ = np.sin(phase) + 0.35 * np.sin(3 * phase) + 0.15 * np.sin(5 * phase)
    wave_ /= np.max(np.abs(wave_))
    fade = np.minimum(1, np.minimum(t / 0.05, (t[-1] - t) / 0.15))  # no clicks at start / end
    return (wave_ * fade * 0.8 * 32767).astype(np.int16)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "static" / "audio" / "ambulance-siren.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(siren().tobytes())
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
