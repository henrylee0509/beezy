"""Render the animated slides in demo/slides.html to MP4 scenes, frame-accurately.

Every frame seeks all CSS animations to t and takes a JPEG screenshot, which is piped into ffmpeg.
Usage:  python demo/render_slides.py OUT_DIR [slide_id ...]
        python demo/render_slides.py --peek OUT_DIR slide_id t1 t2 ...   (write still JPEGs for review)
"""
import asyncio
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
FPS = 30
W, H = 1920, 1080

# slide id -> seconds on screen (fades are added inside each file)
SLIDES = {
    "title": 9.0,
    "problem": 14.0,
    "solution": 12.0,
    "arch": 19.0,
    "bumper": 3.0,
    "trust": 11.0,
    "next": 8.0,
    "close": 7.0,
}

FFMPEG_OUT = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
              "-movflags", "+faststart"]


async def _page(p):
    browser = await p.chromium.launch()
    page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    return browser, page


async def render(slide: str, seconds: float, out: Path):
    tmp = out.with_suffix(".part.mp4")
    fade_out = max(0.0, seconds - 0.45)
    vf = f"fade=t=in:st=0:d=0.35,fade=t=out:st={fade_out:.2f}:d=0.45,format=yuv420p"
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg",
                             "-i", "-", "-vf", vf, *FFMPEG_OUT, str(tmp)], stdin=subprocess.PIPE)
    async with async_playwright() as p:
        browser, page = await _page(p)
        await page.goto(f"file://{HERE / 'slides.html'}?s={slide}")
        await page.evaluate("document.fonts.ready.then(() => 1)")
        await page.wait_for_timeout(200)
        for i in range(int(round(seconds * FPS))):
            await page.evaluate(f"window.seek({i / FPS})")
            proc.stdin.write(await page.screenshot(type="jpeg", quality=92))
        await browser.close()
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {slide}")
    tmp.rename(out)
    print("rendered", out.name)


async def peek(out_dir: Path, slide: str, times):
    async with async_playwright() as p:
        browser, page = await _page(p)
        await page.goto(f"file://{HERE / 'slides.html'}?s={slide}")
        await page.evaluate("document.fonts.ready.then(() => 1)")
        await page.wait_for_timeout(200)
        for t in times:
            await page.evaluate(f"window.seek({t})")
            await page.screenshot(path=str(out_dir / f"peek_{slide}_{t}.jpg"), type="jpeg", quality=85)
        await browser.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--peek":
        out = Path(args[1]); out.mkdir(parents=True, exist_ok=True)
        asyncio.run(peek(out, args[2], [float(x) for x in args[3:]]))
    else:
        out = Path(args[0]); out.mkdir(parents=True, exist_ok=True)
        for s in (args[1:] or SLIDES):
            asyncio.run(render(s, SLIDES[s], out / f"slide_{s}.mp4"))
