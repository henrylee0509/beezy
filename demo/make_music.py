"""Original background music for the demo, synthesised from scratch (no samples, no licensing).

Usage:  python demo/make_music.py OUT.wav [SECONDS]

A bright, light groove in C major at 112 BPM: soft pads and a plucked arpeggio for the intro,
drums and bass join for the product demo, then it thins out and ends on a held chord.
"""
from __future__ import annotations

import sys
import wave

import numpy as np

SR = 44100
BPM = 112
BEAT = 60 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(7)

# I - V - vi - IV, one chord per bar (MIDI notes)
CHORDS = [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]]
ROOTS = [36, 43, 45, 41]


def hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def env(n, a, r):
    e = np.ones(n)
    a, r = min(a, n), min(r, n)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def lowpass(x, cutoff):
    k = 1 - np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):  # one-pole, fine for a few minutes of audio
        acc += k * (v - acc)
        y[i] = acc
    return y


def add(buf, sig, t):
    i = int(t * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def pad_note(n, dur):
    t = np.arange(int(dur * SR)) / SR
    f = hz(n)
    s = sum(np.sin(2 * np.pi * f * d * t + p) for d, p in ((1, 0), (1.004, 1.3), (0.996, 2.1)))
    s += 0.35 * np.sin(2 * np.pi * 2 * f * t)
    return s / 3.4 * env(len(t), int(0.35 * SR), int(0.5 * SR))


def pluck(n, dur=0.45):
    t = np.arange(int(dur * SR)) / SR
    f = hz(n)
    s = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2 * f * t) + 0.15 * np.sin(2 * np.pi * 3 * f * t)
    return s * np.exp(-t * 7.5) * env(len(t), 40, 400)


def bass(n, dur):
    t = np.arange(int(dur * SR)) / SR
    s = np.tanh(1.6 * np.sin(2 * np.pi * hz(n) * t))
    return s * np.exp(-t * 2.2) * env(len(t), 60, 800)


def kick():
    t = np.arange(int(0.32 * SR)) / SR
    f = 48 + 110 * np.exp(-t * 32)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def hat(open_=False):
    t = np.arange(int((0.16 if open_ else 0.05) * SR)) / SR
    noise = rng.standard_normal(len(t))
    noise = noise - lowpass(noise, 6500)
    return noise * np.exp(-t * (22 if open_ else 70))


def clap():
    t = np.arange(int(0.18 * SR)) / SR
    noise = rng.standard_normal(len(t))
    noise = lowpass(noise, 3500) - lowpass(noise, 900)
    return noise * np.exp(-t * 26)


def main():
    out = sys.argv[1]
    total = float(sys.argv[2]) if len(sys.argv) > 2 else 178.0
    n = int(total * SR)
    pads, plk, bas, drm = (np.zeros(n) for _ in range(4))
    duck = np.ones(n)
    bars = int(total / BAR) + 1
    k, h, ho, c = kick(), hat(), hat(True), clap()
    for b in range(bars):
        t0 = b * BAR
        if t0 >= total:
            break
        sec = t0
        chord, root = CHORDS[b % 4], ROOTS[b % 4]
        drums = 20 <= sec < 152          # in from the solution slide to the trust slide
        full = 47 <= sec < 152           # bass and claps under the product demo
        for note in chord[:3]:
            add(pads, pad_note(note, BAR + 0.4), t0)
        # arpeggio, 8th notes; sparser in the intro and outro
        pattern = [0, 2, 1, 3, 2, 1, 3, 2]
        for i, idx in enumerate(pattern):
            if not drums and i % 2:
                continue
            add(plk, pluck(chord[idx] + 12) * (0.9 if i % 2 == 0 else 0.6), t0 + i * BEAT / 2)
        if full:
            for q in range(4):
                add(bas, bass(root + (12 if q == 3 and b % 2 else 0), BEAT * 0.95), t0 + q * BEAT)
        if drums:
            for q in range(4):
                tq = t0 + q * BEAT
                if full or q in (0, 2):
                    add(drm, k * 0.9, tq)
                    i = int(tq * SR)
                    dl = min(n - i, int(0.22 * SR))
                    if dl > 0:  # sidechain: pads and bass dip under each kick
                        duck[i:i + dl] = np.minimum(duck[i:i + dl], 0.55 + 0.45 * np.linspace(0, 1, dl) ** 0.7)
                add(drm, h * 0.22, tq + BEAT / 2)
                if full and q in (1, 3):
                    add(drm, c * 0.35, tq)
                if full and q == 3 and b % 2:
                    add(drm, ho * 0.18, tq + BEAT * 0.75)
    pads = lowpass(pads, 2400)
    mix = 0.30 * pads * duck + 0.24 * plk + 0.42 * bas * duck + 0.55 * drm
    # final chord rings out under the close
    end_t = total - 7.5
    for note in CHORDS[0]:
        add(mix, 0.25 * pad_note(note, 7.0), end_t)
    # gentle stereo: delay the pluck a little on the right
    d = int(0.012 * SR)
    left = mix
    right = mix.copy()
    right[d:] = mix[:-d] * 0.85 + 0.15 * mix[d:]
    st = np.stack([left, right], axis=1)
    fade_in, fade_out = int(1.5 * SR), int(3.5 * SR)
    st[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    st[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    st /= np.max(np.abs(st)) / 0.89
    pcm = (st * 32767).astype("<i2")
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"wrote {out} ({total:.1f}s)")


if __name__ == "__main__":
    main()
