"""Meta Model API client (Muse Spark chat completions with tool calling + Muse Voice Transcribe).

Uses plain HTTPS (OpenAI-compatible Chat Completions format at https://api.meta.ai/v1).
API keys stay on the server; every request is logged to ai_requests.
"""
from __future__ import annotations

import json
import time

import requests

from .. import config, db


class MetaAPIError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _log(biz, run_id, agent, model, mode, status, latency_ms, usage=None, error=None):
    try:
        with db.connect() as conn:
            db.insert(conn, "ai_requests", {
                "business_id": biz, "run_id": run_id, "agent": agent, "model": model, "mode": mode, "status": status,
                "latency_ms": latency_ms, "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"), "error": (error or "")[:500] or None})
    except Exception:
        pass


def _headers(extra: dict | None = None) -> dict:
    if not config.META_API_KEY:
        raise MetaAPIError("MODEL_API_KEY is not configured")
    h = {"Authorization": f"Bearer {config.META_API_KEY}"}
    h.update(extra or {})
    return h


def chat(messages: list[dict], tools: list[dict] | None = None, *, biz: int | None = None, run_id: str | None = None,
         agent: str = "assistant", max_tokens: int | None = None, temperature: float | None = None) -> dict:
    """Call /chat/completions. Returns {"message": {...}, "usage": {...}, "latency_ms": int, "model": str}."""
    body: dict = {"model": config.META_MODEL, "messages": messages}
    if tools:
        body["tools"] = tools
        body["parallel_tool_calls"] = True
    if max_tokens:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if config.META_REASONING_EFFORT:
        body["reasoning_effort"] = config.META_REASONING_EFFORT
    url = f"{config.META_BASE_URL}/chat/completions"
    last_err = None
    for attempt in range(config.META_MAX_RETRIES):
        t0 = time.monotonic()
        try:
            r = requests.post(url, headers=_headers({"Content-Type": "application/json"}), data=json.dumps(body),
                              timeout=config.META_TIMEOUT_SEC)
            latency = int((time.monotonic() - t0) * 1000)
            if r.status_code == 200:
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                usage = data.get("usage") or {}
                _log(biz, run_id, agent, data.get("model", config.META_MODEL), "real", "ok", latency, usage)
                return {"message": msg, "usage": usage, "latency_ms": latency, "model": data.get("model", config.META_MODEL),
                        "finish_reason": choice.get("finish_reason")}
            text = r.text[:600]
            last_err = MetaAPIError(f"Meta API HTTP {r.status_code}: {text}", r.status_code)
            _log(biz, run_id, agent, config.META_MODEL, "real", f"http_{r.status_code}", latency, error=text)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(8, 1.5 * 2 ** attempt))
                continue
            raise last_err
        except requests.RequestException as e:
            latency = int((time.monotonic() - t0) * 1000)
            last_err = MetaAPIError(f"Network error calling Meta API: {e}")
            _log(biz, run_id, agent, config.META_MODEL, "real", "network_error", latency, error=str(e))
            time.sleep(min(8, 1.5 * 2 ** attempt))
    raise last_err or MetaAPIError("Meta API request failed")


def transcribe(wav_bytes: bytes, *, biz: int | None = None) -> str:
    """Muse Voice Transcribe: POST /asr/transcribe (multipart: JSON settings part + WAV audio part)."""
    settings = {"model": config.META_ASR_MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    files = {
        "request": (None, json.dumps(settings), "application/json"),
        "audio": ("recording.wav", wav_bytes, "audio/wav"),
    }
    t0 = time.monotonic()
    try:
        r = requests.post(f"{config.META_BASE_URL}/asr/transcribe", headers=_headers({"Accept": "text/plain"}),
                          files=files, timeout=config.META_TIMEOUT_SEC)
    except requests.RequestException as e:
        _log(biz, None, "voice", config.META_ASR_MODEL, "real", "network_error", None, error=str(e))
        raise MetaAPIError(f"Network error calling Meta ASR: {e}")
    latency = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        _log(biz, None, "voice", config.META_ASR_MODEL, "real", f"http_{r.status_code}", latency, error=r.text[:500])
        raise MetaAPIError(f"Transcription failed (HTTP {r.status_code}): {r.text[:300]}", r.status_code)
    _log(biz, None, "voice", config.META_ASR_MODEL, "real", "ok", latency)
    lines = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line and line.split(":", 1)[0] in ("unknown",) or (len(line) > 2 and line[1] == ":" and line[0].isalpha()):
            line = line.split(":", 1)[1].strip()  # strip diarization speaker labels
        lines.append(line)
    return " ".join(lines).strip()


def parse_json(text: str) -> dict:
    """Extract the first JSON object from model text (handles ```json fences)."""
    if not text:
        raise ValueError("empty response")
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("\n") + 1:] if "\n" in s else s
    start = s.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError("unterminated JSON object")
