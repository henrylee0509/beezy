#!/usr/bin/env bash
# Re-records the whole demo with live Muse Spark. Needs MODEL_API_KEY in .env, Playwright and ffmpeg.
#   bash demo/record_live.sh            -> Beezy_demo_live.mp4
set -euo pipefail
cd "$(dirname "$0")/.."
grep -q '^MODEL_API_KEY=.\+' .env || { echo "Set MODEL_API_KEY in .env first"; exit 1; }
OUT=out_live; rm -rf "$OUT" /tmp/bp-video-live; mkdir -p "$OUT"
BP_DATA_DIR=/tmp/bp-video-live PORT=8765 BP_AUTOMATION_INTERVAL_SEC=0 python run.py > "$OUT/server.log" 2>&1 &
SERVER=$!; trap 'kill $SERVER 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf localhost:8765/api/health && break; sleep 1; done; echo
python demo/record_footage.py "$OUT" --url http://localhost:8765
python demo/render_slides.py "$OUT"
python demo/build_video.py "$OUT" Beezy_demo_live.mp4
echo "Done: Beezy_demo_live.mp4 (timeline in $OUT/timeline.txt)"
