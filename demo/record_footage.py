"""Record real product footage of Beezy for the demo video.

Drives the running app with Playwright, captures it through Chrome's screencast (real timing, ~30 fps),
and draws a visible cursor, captions and camera zooms into the page. Each segment becomes one MP4.

Start the app first (fresh demo data recommended), e.g.
    BP_DATA_DIR=/tmp/bp-video BP_MOCK_STEP_DELAY_MS=420 PORT=8765 python run.py
With MODEL_API_KEY set, the footage shows live Muse Spark; slow model turns are sped up to fit.

Usage:  python demo/record_footage.py OUT_DIR [--url http://localhost:8765] [segment ...]
"""
import argparse
import asyncio
import base64
import math
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VW, VH, DSF = 1600, 900, 1.2          # CSS viewport; frames come out at 1920x1080
FPS = 30

INJECT = r"""
(() => {
  const css = `
    ::-webkit-scrollbar { width: 0; height: 0; }
    /* sticky chrome misplaces under the camera transform, so it scrolls normally while filming */
    .topbar, .sidebar, .floor { position: relative !important; top: auto !important; }
    .sidebar { height: auto !important; }
    #demo-cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 2147483647; pointer-events: none;
      transform: translate(-3px, -2px); transition: none; filter: drop-shadow(0 2px 4px rgba(0,0,0,.35)); }
    .demo-ripple { position: fixed; z-index: 2147483646; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
      border: 3px solid #e0a100; pointer-events: none; animation: demo-rip .55s ease-out forwards; }
    @keyframes demo-rip { to { transform: scale(4.2); opacity: 0; } }
    #demo-cap { position: fixed; left: 28px; bottom: 26px; z-index: 2147483645; display: flex; gap: 12px; align-items: center;
      background: rgba(22, 20, 15, .95); color: #fbf8ef; padding: 13px 20px 13px 16px; border-radius: 14px;
      font: 600 19px/1.3 "Inter", sans-serif; box-shadow: 0 10px 30px rgba(0,0,0,.25); max-width: 760px;
      opacity: 0; transform: translateY(16px); transition: opacity .35s ease, transform .35s ease; pointer-events: none; }
    #demo-cap.on { opacity: 1; transform: none; }
    #demo-cap i { width: 10px; height: 10px; border-radius: 50%; background: #f5b301; flex: none; }
    #demo-cap small { display: block; font-weight: 500; font-size: 15px; color: #b5ad9c; margin-top: 2px; }
    body.demo-cam { transition: transform .9s cubic-bezier(.45, .05, .2, 1); transform-origin: 0 0; }
  `;
  const install = () => {
    if (document.getElementById('demo-cursor')) return;
    const st = document.createElement('style'); st.textContent = css; document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = 'demo-cursor';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2 L4 19 L8.6 14.6 L11.6 21.4 L14.4 20.2 L11.4 13.5 L17.8 13.5 Z" fill="#fff" stroke="#16140f" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    const cap = document.createElement('div'); cap.id = 'demo-cap'; document.documentElement.appendChild(cap);
    const pos = window.__demoPos || { x: 800, y: 450 };
    c.style.left = pos.x + 'px'; c.style.top = pos.y + 'px';
    document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; window.__demoPos = { x: e.clientX, y: e.clientY }; }, true);
    document.addEventListener('mousedown', e => {
      const r = document.createElement('div'); r.className = 'demo-ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700);
    }, true);
  };
  window.__cap = (t, s) => { install(); const el = document.getElementById('demo-cap');
    if (!t) { el.classList.remove('on'); return; }
    el.innerHTML = '<i></i><div>' + t + (s ? '<small>' + s + '</small>' : '') + '</div>'; el.classList.add('on'); };
  window.__cam = (x, y, s) => {
    const b = document.body; b.classList.add('demo-cam');
    if (!s || s === 1) { b.style.transform = 'translate(0px, 0px) scale(1)'; setTimeout(() => { if (b.style.transform.includes('scale(1)')) b.style.transform = ''; }, 950); return; }
    // x, y are viewport coordinates; the body is transformed in document space, so add the scroll offset.
    const W = innerWidth, H = innerHeight, sx = scrollX, sy = scrollY;
    const docW = Math.max(W, document.documentElement.scrollWidth), docH = Math.max(H, document.documentElement.scrollHeight);
    let tx = W / 2 + sx - (x + sx) * s, ty = H / 2 + sy - (y + sy) * s;
    tx = Math.min(sx, Math.max(sx + W - docW * s, tx));
    ty = Math.min(sy, Math.max(sy + H - docH * s, ty));
    b.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""


class Recorder:
    def __init__(self, page, cdp, out: Path):
        self.page, self.cdp, self.out = page, cdp, out
        self.frames = None
        self.cursor = (VW / 2, VH / 2)

    async def start_cast(self):
        def on_frame(p):
            if self.frames is not None:
                self.frames.append((p["metadata"].get("timestamp", time.time()), base64.b64decode(p["data"])))
            asyncio.create_task(self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
        self.cdp.on("Page.screencastFrame", on_frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080,
                                                     "everyNthFrame": 1})

    async def clip_start(self):
        first = await self.page.screenshot(type="jpeg", quality=92)
        self.frames = [(time.time(), first)]
        self.t0 = time.time()

    async def clip_end(self, name: str, max_seconds: float | None = None, hold: float = 0.6):
        await asyncio.sleep(hold)
        t1 = time.time()
        frames, self.frames = self.frames, None
        frames = [f for f in frames if f[0] >= self.t0 - 0.05]
        frames.sort(key=lambda f: f[0])
        raw = t1 - frames[0][0]
        speed = max(1.0, raw / max_seconds) if max_seconds else 1.0
        work = self.out / f"_{name}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        lines = []
        for i, (ts, data) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else t1
            (work / f"f{i:05d}.jpg").write_bytes(data)
            lines += [f"file 'f{i:05d}.jpg'", f"duration {max(0.001, (nxt - ts) / speed):.4f}"]
        lines.append(f"file 'f{len(frames) - 1:05d}.jpg'")
        (work / "list.txt").write_text("\n".join(lines))
        dur = raw / speed
        tmp = self.out / f"clip_{name}.part.mp4"
        vf = (f"fps={FPS},scale=1920:1080:flags=lanczos,format=yuv420p,"
              f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, dur - 0.35):.2f}:d=0.35")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
                            "-movflags", "+faststart", str(tmp)])
        if r.returncode != 0:
            raise SystemExit(f"ffmpeg failed on {name}")
        tmp.rename(self.out / f"clip_{name}.mp4")
        shutil.rmtree(work, ignore_errors=True)
        print(f"clip {name}: {raw:.1f}s raw -> {dur:.1f}s (x{speed:.2f}), {len(frames)} frames")

    # ---- human-like input
    async def move(self, x, y, ms=650):
        x0, y0 = self.cursor
        steps = max(8, int(ms / 16))
        for i in range(1, steps + 1):
            t = i / steps
            e = 0.5 - math.cos(math.pi * t) / 2
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await asyncio.sleep(ms / 1000 / steps)
        self.cursor = (x, y)

    async def move_to(self, locator, ms=650, dx=0.5, dy=0.5):
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        await self.move(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy, ms)
        return box

    async def click(self, locator, ms=650, pause=0.15):
        await self.move_to(locator, ms)
        await asyncio.sleep(pause)
        await self.page.mouse.down()
        await asyncio.sleep(0.07)
        await self.page.mouse.up()

    async def cap(self, text=None, sub=None):
        await self.page.evaluate("([t, s]) => window.__cap(t, s)", [text, sub])

    async def cam(self, locator=None, scale=1.0):
        if locator is None or scale == 1:
            await self.page.evaluate("() => window.__cam(0, 0, 1)")
        else:
            box = await locator.bounding_box()
            await self.page.evaluate("([x, y, s]) => window.__cam(x, y, s)",
                                     [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, scale])
        await asyncio.sleep(1.0)

    async def wait_answer(self, timeout=240):
        """Wait until the assistant run finishes (send button re-enabled)."""
        await self.page.wait_for_function("() => { const b = document.querySelector('#sendBtn'); return b && !b.disabled; }",
                                          timeout=timeout * 1000)


async def seg_overview(r: Recorder, url):
    p = r.page
    await p.goto(url + "/")
    await p.wait_for_selector("[data-demo]")
    await r.clip_start()
    await asyncio.sleep(0.8)
    await r.cap("Five roles, one business", "Each person sees only what their role allows")
    await r.click(p.locator('[data-demo="owner@brewbean.demo"]'), 900)
    await p.wait_for_selector(".briefing .ai-prose", timeout=60000)
    await asyncio.sleep(0.6)
    await r.cap("A morning briefing from the Business Intelligence agent", "Every figure is checked against the records")
    await r.move_to(p.locator(".briefing .ai-prose"), 800, 0.6, 0.4)
    await r.cam(p.locator("#briefing"), 1.45)
    await asyncio.sleep(2.6)
    await r.move_to(p.locator(".verify").first, 700)
    await asyncio.sleep(1.2)
    await r.cam(None)
    await r.cap("Live KPIs, alerts and forecasts across every department")
    await p.mouse.wheel(0, 520)
    await asyncio.sleep(1.6)
    await p.mouse.wheel(0, 560)
    await asyncio.sleep(1.8)
    await r.cap(None)
    await r.clip_end("overview", max_seconds=14)


async def seg_health(r: Recorder, url):
    p = r.page
    await p.evaluate("() => window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)
    await r.clip_start()
    await r.click(p.locator('a:has-text("Run a health check")'), 800)
    await p.wait_for_selector("#execDesk")
    await r.cap("The Executive Assistant brings in the departments it needs",
                "Each one is its own Muse Spark agent with real tools" if LIVE else "Each department is its own agent with real tools")
    await asyncio.sleep(0.5)
    await r.cam(p.locator(".floor"), 1.35)
    await r.wait_answer()
    await asyncio.sleep(1.2)
    await r.cam(None)
    await r.cap("One consolidated answer, grounded in the business's own data")
    msg = p.locator(".msg.assistant").last
    await r.move_to(msg, 700, 0.5, 0.2)
    await p.mouse.wheel(0, 700)
    await asyncio.sleep(1.8)
    await p.mouse.wheel(0, 900)
    await asyncio.sleep(1.2)
    await r.cap("Figures traced back to tool results", "Anything the verifier can't trace is flagged")
    v = p.locator(".msg.assistant .verify").last
    await r.move_to(v, 700)
    await r.cam(v, 1.8)
    await asyncio.sleep(1.8)
    await r.cam(None)
    await r.cap(None)
    await r.clip_end("health", max_seconds=21)


async def seg_restock(r: Recorder, url):
    p = r.page
    await r.clip_start()
    box = p.locator("#prompt")
    await r.click(box, 700)
    await r.cap("Ask it to act, not just answer")
    await p.keyboard.type("Restock everything below 20% and make sure it arrives before we run out.", delay=28)
    await asyncio.sleep(0.4)
    await p.keyboard.press("Enter")
    await asyncio.sleep(0.6)
    await r.cam(p.locator(".floor"), 1.3)
    await r.wait_answer()
    await asyncio.sleep(0.8)
    await r.cam(None)
    cards = p.locator(".msg.assistant").last.locator(".action-card")
    await cards.first.scroll_into_view_if_needed()
    await asyncio.sleep(0.6)
    await r.cap("Agents only draft. The owner approves.", "Orders above the manager's limit need the owner")
    await r.cam(cards.nth(1), 1.45)
    await asyncio.sleep(1.4)
    await r.cam(None)
    metro = p.locator('.msg.assistant .action-card:has-text("Metro Dairy") [data-approve]').last
    await r.click(metro, 800)
    await asyncio.sleep(1.8)
    await r.cap(None)
    await r.clip_end("restock", max_seconds=16)


async def seg_supplier(r: Recorder, url):
    p = r.page
    await r.clip_start()
    await r.click(p.locator('#nav a[data-path="inventory"]'), 700)
    await p.wait_for_selector("[data-detail]")
    await asyncio.sleep(0.6)
    await r.cap("Why Highland and not the cheapest supplier?")
    await r.click(p.locator('[data-detail]:has-text("Coffee Beans")'), 800)
    await p.wait_for_selector("#pd table")
    await asyncio.sleep(0.8)
    tbl = p.locator("#pd table").first
    await r.move_to(tbl, 700, 0.5, 0.4)
    await r.cap("The cheaper supplier arrives after the projected stockout", "Forecast and comparison are deterministic code")
    await r.cam(tbl, 1.4)
    await asyncio.sleep(2.6)
    await r.cam(None)
    await p.keyboard.press("Escape")
    await r.cap(None)
    await r.clip_end("supplier", max_seconds=11)


async def seg_contract(r: Recorder, url):
    p = r.page
    await r.clip_start()
    await r.click(p.locator('#nav a[data-path="contracts"]'), 700)
    await p.wait_for_selector("#tbl table")
    await r.cap("Upload a new supplier agreement")
    await r.move_to(p.locator('label:has-text("Upload contract")'), 700)
    await asyncio.sleep(0.4)
    await p.locator("#up").set_input_files(str(ROOT / "sample_docs" / "FreshBake_Pastry_Supply_Agreement_DEMO.pdf"))
    await p.wait_for_selector("#cd h2", timeout=120000)
    await asyncio.sleep(0.8)
    await r.cap("The Contract agent extracts the terms, with page references", "Reminders are scheduled before the notice deadline")
    kv = p.locator("#cd .kv").first
    await r.move_to(kv, 700)
    await r.cam(kv, 1.35)
    await asyncio.sleep(2.0)
    await r.cam(None)
    clause = p.locator('#cd .action-card:has-text("Auto-renewal")').first
    if await clause.count():
        await r.move_to(clause, 700)
        await asyncio.sleep(1.2)
    await p.keyboard.press("Escape")
    await r.cap(None)
    await r.clip_end("contract", max_seconds=13)


async def seg_payroll(r: Recorder, url):
    p = r.page
    await r.clip_start()
    await r.click(p.locator('#nav a[data-path="payroll"]'), 700)
    await p.wait_for_selector(".kpis")
    await asyncio.sleep(0.6)
    await r.cap("Payroll is calculated in code, never by the model")
    await r.move_to(p.locator("table.t").first, 700, 0.5, 0.3)
    await asyncio.sleep(1.0)
    checks = p.locator('section:has-text("Checks before payday")')
    await r.cap("Problems caught before payday", "Missing hours and missing bank details")
    await r.move_to(checks, 700, 0.5, 0.3)
    await r.cam(checks, 1.4)
    await asyncio.sleep(2.2)
    await r.cam(None)
    await r.cap(None)
    await r.clip_end("payroll", max_seconds=9)


async def seg_automation(r: Recorder, url):
    p = r.page
    await r.clip_start()
    await r.click(p.locator('#nav a[data-path="inventory"]'), 700)
    await p.wait_for_selector("[data-adjust]")
    await r.cap("Automations react the moment something changes")
    oat = p.locator('tr:has-text("Oat Milk") [data-adjust]')
    await r.click(oat, 800)
    await p.wait_for_selector("#f_qty")
    await p.select_option("#f_kind", "sale")
    await r.click(p.locator("#f_qty"), 500)
    await p.keyboard.type("50", delay=60)
    await r.click(p.locator('.modal button[type="submit"]'), 600)
    await asyncio.sleep(0.6)
    await r.cap("Oat milk crossed 20%, so a purchase order was drafted for approval")
    await r.move(VW - 260, VH - 90, 700)
    await asyncio.sleep(2.4)
    await r.move_to(p.locator('a[href="#/approvals"].icon-btn'), 700)
    await asyncio.sleep(1.0)
    await r.cap(None)
    await r.clip_end("automation", max_seconds=9)


async def seg_activity(r: Recorder, url):
    p = r.page
    await r.clip_start()
    await r.click(p.locator('#nav a[data-path="activity"]'), 700)
    await p.wait_for_selector("#tbl table")
    await r.click(p.locator('[data-a="AGENT"]'), 600)
    await asyncio.sleep(0.4)
    await r.cap("Every agent action is in the audit trail")
    await asyncio.sleep(2.2)
    await r.cap(None)
    await r.clip_end("activity", max_seconds=5)


LIVE = False  # set from /api/health: captions never claim Muse Spark when the app runs in mock mode

SEGMENTS = {"overview": seg_overview, "health": seg_health, "restock": seg_restock, "supplier": seg_supplier,
            "contract": seg_contract, "payroll": seg_payroll, "automation": seg_automation, "activity": seg_activity}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("segments", nargs="*")
    ap.add_argument("--url", default="http://localhost:8765")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    global LIVE
    import json
    import urllib.request
    with urllib.request.urlopen(a.url + "/api/health", timeout=10) as resp:
        LIVE = json.load(resp).get("ai_mode") == "real"
    print("AI mode:", "live Muse Spark" if LIVE else "mock")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": VW, "height": VH}, device_scale_factor=DSF)
        await ctx.add_init_script(INJECT)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        r = Recorder(page, cdp, out)
        await r.start_cast()
        for name in (a.segments or SEGMENTS):
            await SEGMENTS[name](r, a.url)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
