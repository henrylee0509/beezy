"""Build subtitles (SRT + styled ASS) for the demo from the narration script.

Usage:  python demo/make_subtitles.py lines.json OUT_DIR [CLIPS_DIR]

Each scene's line is split into short readable cues. With CLIPS_DIR (the recorded narration clips),
cue timing follows how long each line takes to say; otherwise it assumes 2.7 words per second.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LEAD, GAP, MAX_TEMPO, WPS = 0.3, 0.25, 1.10, 2.9
MAX_WORDS = 11


def duration(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True, check=True).stdout.strip())


def chunks(text: str) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        words = sent.split()
        if len(words) <= MAX_WORDS:
            out.append(sent)
            continue
        # split into the fewest balanced pieces, nudging each break to nearby punctuation
        k = -(-len(words) // MAX_WORDS)
        size = len(words) / k
        parts, i = [], 0
        for j in range(1, k):
            cut = round(j * size)
            for off in (0, 1, -1, 2, -2):
                if i < cut + off < len(words) and words[cut + off - 1].endswith((",", ":", ";")):
                    cut += off
                    break
            parts.append(" ".join(words[i:cut]))
            i = cut
        parts.append(" ".join(words[i:]))
        out += parts
    # merge very short pieces with their neighbour so nothing flashes by
    merged = []
    for piece in out:
        if merged and (len(piece.split()) < 4 or len(merged[-1].split()) < 4) \
                and len(merged[-1].split()) + len(piece.split()) <= MAX_WORDS:
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return merged


def ts(t: float, ass=False) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    if ass:
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def main():
    lines = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    clips = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    cues, prev_end = [], 0.0
    for i, line in enumerate(lines):
        start = max(line["start"] + LEAD, prev_end + GAP)
        slot_end = lines[i + 1]["start"] - 0.15 if i + 1 < len(lines) else line["start"] + 8
        clip = next((p for p in clips.glob(f"{line['id']}.*")), None) if clips else None
        d = duration(clip) if clip else len(line["text"].split()) / WPS
        d /= min(MAX_TEMPO, max(1.0, d / max(0.5, slot_end - start)))
        prev_end = start + d
        parts = chunks(line["text"])
        total_chars = sum(len(p) for p in parts)
        t = start
        for p in parts:
            dt = max(1.2, d * len(p) / total_chars)
            cues.append((t, t + dt - 0.04, p))
            t += dt
        prev_end = max(prev_end, t)
    a, b, txt = cues[-1]
    cues[-1] = (a, max(b, a + 5.0), txt)      # let the closing line linger
    # keep cues from overlapping after the minimum-duration bump
    for k in range(len(cues) - 1):
        a, b, txt = cues[k]
        cues[k] = (a, min(b, cues[k + 1][0] - 0.04), txt)
    srt = "\n".join(f"{n}\n{ts(a)} --> {ts(b)}\n{txt}\n" for n, (a, b, txt) in enumerate(cues, 1))
    (out / "Beezy_demo.srt").write_text(srt, encoding="utf-8")
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           # white Inter on a soft ink box (ASS colours are &HAABBGGRR)
           "Style: Sub,Inter SemiBold,40,&H00FFFFFF,&H00FFFFFF,&H2E0F1416,&H2E0F1416,0,0,0,0,100,100,0,0,3,14,0,2,300,300,30,1",
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ass += [f"Dialogue: 0,{ts(a, True)},{ts(b, True)},Sub,,0,0,0,,{txt}" for a, b, txt in cues]
    (out / "Beezy_demo.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")
    print(f"{len(cues)} cues, last ends {cues[-1][1]:.1f}s")


if __name__ == "__main__":
    main()
