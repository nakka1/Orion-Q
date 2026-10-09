"""Rebuild OrionQ's original EVA display tone using only Python's stdlib.

Two soft, descending frequency-modulated pulses and a low tail form a short
science-fiction interface cue. No recordings, samples or network input are used.
"""
from __future__ import annotations

from array import array
from math import exp, sin, tau
from pathlib import Path
import sys
import wave


SAMPLE_RATE = 44100
DURATION_SECONDS = 2.1
OUTPUT = Path(__file__).resolve().parents[1] / "src/quantum_autopilot/console/assets/emergency-interface.wav"


def tone_pcm() -> bytes:
    samples = array("h")
    for index in range(round(SAMPLE_RATE * DURATION_SECONDS)):
        time = index / SAMPLE_RATE
        value = 0.
        for onset in (.02, .67):
            age = time - onset
            if 0. <= age < .62:
                envelope = (1. - exp(-age * 44.)) * exp(-age * 5.6)
                envelope *= min(1., (.62 - age) / .08)
                phase = tau * (520. * age - 205. * age ** 2)
                modulation = .9 * sin(tau * 19. * age) * exp(-age * 5.)
                value += .25 * envelope * (sin(phase + modulation) + .22 * sin(phase * 2.01))
        tail_age = time - .15
        if tail_age > 0:
            tail_envelope = .042 * (1. - exp(-tail_age * 9.)) * exp(-tail_age * 2.4)
            value += tail_envelope * sin(tau * (110. * tail_age - 8. * tail_age ** 2))
        # Smooth end preserves silence at the file boundary, even on restart.
        value *= min(1., (DURATION_SECONDS - time) / .14)
        samples.append(round(max(-1., min(1., value)) * 32767))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(tone_pcm())
    print(f"Generated original tone: {OUTPUT}")


if __name__ == "__main__":
    main()
