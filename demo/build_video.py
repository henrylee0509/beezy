"""Join slide scenes and product footage into the final demo video.

Usage:  python demo/build_video.py WORK_DIR [OUTPUT.mp4]
WORK_DIR must contain slide_*.mp4 (render_slides.py) and clip_*.mp4 (record_footage.py).
Writes the video plus a timeline (timeline.txt) for recording the voice-over.
"""
import json
import subprocess
import sys
from pathlib import Path

ORDER = ["slide_title", "slide_problem", "slide_solution", "slide_arch", "slide_bumper",
         "clip_overview", "clip_health", "clip_restock", "clip_supplier", "clip_contract", "clip_payroll",
         "clip_automation", "clip_activity", "slide_trust", "slide_next", "slide_close"]


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def main():
    work = Path(sys.argv[1])
    final = Path(sys.argv[2]) if len(sys.argv) > 2 else work / "Beezy_demo.mp4"
    files = [work / f"{n}.mp4" for n in ORDER]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing scenes: {missing}")
    (work / "concat.txt").write_text("".join(f"file '{f.resolve()}'\n" for f in files))
    t, lines = 0.0, []
    for f in files:
        d = duration(f)
        lines.append(f"{int(t // 60)}:{t % 60:05.2f}  {f.stem}  ({d:.1f}s)")
        t += d
    (work / "timeline.txt").write_text("\n".join(lines) + f"\nTotal {t:.1f}s\n")
    video_only = work / "_joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "concat.txt"),
                    "-c", "copy", str(video_only)], check=True)
    tmp = final.with_suffix(".part.mp4")
    # A silent stereo track keeps editors and upload sites happy until the voice-over is added.
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_only), "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(tmp)], check=True)
    tmp.rename(final)
    video_only.unlink()
    print((work / "timeline.txt").read_text())
    print("wrote", final)


if __name__ == "__main__":
    main()
