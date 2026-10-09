"""Rebuild OrionQ's original energy-down cue using only Python's stdlib.

Descending oscillators and softly fading electronic clicks suggest a system
powering down. This is original synthesis; no recordings or external samples
are read. Generation is deterministic and playback is optional UI decoration.
"""
from __future__ import annotations

from array import array
from math import exp, sin, tau
from pathlib import Path
import sys
import wave


SAMPLE_RATE = 44100
DURATION_SECONDS = 3.2
OUTPUT = Path(__file__).resolve().parents[1] / "src/quantum_autopilot/console/assets/disconnect-interface.wav"


def _phase(time: float, start_hz: float, end_hz: float, decay: float) -> float:
    """Integrate a strictly descending frequency so phase remains continuous."""
    return tau * (end_hz * time + (start_hz - end_hz) * (1. - exp(-decay * time)) / decay)


def tone_pcm() -> bytes:
    samples = array("h")
    count = round(SAMPLE_RATE * DURATION_SECONDS)
    for index in range(count):
        time = index / SAMPLE_RATE
        attack = min(1., time / .04)
        attack = attack * attack * (3. - 2. * attack)
        fade = min(1., (count - 1 - index) / (SAMPLE_RATE * .44))
        fade = fade * fade * (3. - 2. * fade)
        envelope = attack * exp(-time * .48) * fade
        phase = _phase(time, 760., 48., 1.2)
        value = envelope * (.38 * sin(phase) + .10 * sin(phase * 2.))
        value += envelope * .13 * sin(_phase(time, 160., 34., .9))
        for onset in (.18, .85, 1.6):
            age = time - onset
            if 0. <= age < .11:
                pulse = (1. - exp(-age * 600.)) * exp(-age * 65.)
                value += .06 * pulse * sin(_phase(age, 1450., 130., 26.))
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
    print(f"Generated original shutdown tone: {OUTPUT}")


if __name__ == "__main__":
    main()
