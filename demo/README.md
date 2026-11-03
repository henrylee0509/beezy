# Demo video pipeline

Builds the 3-minute submission video from two sources:

- **Slides:** `slides.html`, animated in the product's own design system (same fonts, colours and icons as the app), rendered frame-by-frame by `render_slides.py`.
- **Real product footage:** `record_footage.py` drives the running app in a headless browser, with a visible cursor, captions and camera zooms, and records each scene.

`build_video.py` joins everything into one MP4 with a silent audio track, ready for your voice-over. The narration script, timed to the cut, is in [`VOICEOVER.md`](VOICEOVER.md).

## Re-record with live Muse Spark (recommended before submitting)

Footage recorded without an API key shows the app's labelled **mock mode**. To show Muse Spark actually running the agents, record on a machine that can reach `api.meta.ai`:

```bash
pip install -r requirements.txt playwright && python -m playwright install chromium   # also needs ffmpeg on PATH
export MODEL_API_KEY=your-meta-model-api-key
export BP_DATA_DIR=/tmp/bp-video PORT=8765 BP_AUTOMATION_INTERVAL_SEC=0
rm -rf /tmp/bp-video && python run.py &          # fresh demo data every take
python demo/record_footage.py out/               # ~2 minutes; prints "AI mode: live Muse Spark"
python demo/render_slides.py out/                # ~4 minutes
python demo/build_video.py out/ Beezy_demo.mp4
```

- **Captions** follow the AI mode, so they never claim Muse Spark while the app is in mock mode.
- **Long model turns** are sped up to fit each scene's time slot (the speed-up is printed per clip). The answer and the operations-floor activity are real.
- **Mock-mode pacing:** `BP_MOCK_STEP_DELAY_MS=420` only slows the mock planner so its steps are visible on camera. It has no effect in live mode.
- **Re-recording one scene:** run `python demo/record_footage.py out/ health`, then rebuild.
