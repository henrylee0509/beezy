"""Mix per-scene narration clips into the demo video.

Usage:  python demo/add_voiceover.py CLIPS_DIR_OR_ZIP lines.json Beezy_demo.mp4 Beezy_final.mp4 [--bright]

--bright lifts pitch slightly, adds presence and treble, and quickens the pace a little.

CLIPS_DIR holds 01.mp3/.wav, 02..., one per entry in lines.json ({"id", "start", "text"}).
Each clip starts 0.3 s after its scene; a clip that would run into the next scene is sped up
slightly (at most 1.10x); the next line then waits until it has finished.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

LEAD = 0.3
BRIGHT = ("rubberband=pitch=1.08:tempo=1.05:formant=preserved,highpass=f=90,equalizer=f=3000:t=q:w=1.2:g=3.5,"
          "treble=g=3:f=7000,acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,")
MAX_TEMPO = 1.10
GAP = 0.25


def duration(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True, check=True).stdout.strip())


def main():
    bright = "--bright" in sys.argv
    src, lines_p, video, final = (Path(a) for a in [a for a in sys.argv[1:] if a != "--bright"][:4])
    if src.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp())
        zipfile.ZipFile(src).extractall(tmp)
        src = tmp
    lines = json.loads(lines_p.read_text(encoding="utf-8"))
    total = duration(video)
    inputs, filters, labels = [], [], []
    prev_end = 0.0
    for i, line in enumerate(lines):
        clip = next((p for p in src.rglob(f"{line['id']}.*") if p.suffix in (".mp3", ".wav", ".m4a")), None)
        if clip is None:
            print(f"missing clip {line['id']} ({line['scene']}), skipped")
            continue
        start = max(line["start"] + LEAD, prev_end + GAP)   # never overlap the previous line
        end = lines[i + 1]["start"] - 0.15 if i + 1 < len(lines) else total - 0.4
        d = duration(clip) / (1.05 if bright else 1.0)
        tempo = min(MAX_TEMPO, max(1.0, d / max(0.5, end - start)))
        prev_end = start + d / tempo
        k = len(inputs) // 2
        inputs += ["-i", str(clip)]
        ms = int(start * 1000)
        filters.append(f"[{k + 1}:a]aresample=48000,{BRIGHT if bright else ''}atempo={tempo:.3f},adelay={ms}|{ms},apad[a{k}]")
        labels.append(f"[a{k}]")
        print(f"{line['id']} {line['scene']:<22} {d:5.1f}s at {start:6.1f}s  slot {end - start:5.1f}s  x{tempo:.2f}"
              + ("  (runs over)" if d / tempo > end - start + 0.05 else ""))
    mix = (";".join(filters) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
           f"loudnorm=I=-16:TP=-1.5:LRA=11,atrim=0:{total:.3f}[vo]")
    tmp_out = final.with_name(final.stem + ".tmp.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *inputs, "-filter_complex", mix,
                    "-map", "0:v", "-map", "[vo]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    str(tmp_out)], check=True)
    tmp_out.replace(final)
    print(f"wrote {final} ({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
