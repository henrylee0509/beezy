"""Runtime configuration, loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

try:  # python-dotenv is optional
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # pragma: no cover
    pass

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("BP_DATA_DIR", ROOT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = os.getenv("BP_DATABASE_PATH", str(DATA_DIR / "businesspilot.db"))
FRONTEND_DIR = ROOT_DIR / "frontend"

_secret_file = DATA_DIR / ".jwt_secret"
if os.getenv("BP_JWT_SECRET"):
    JWT_SECRET = os.environ["BP_JWT_SECRET"]
else:  # generate once per install so tokens survive restarts without a hardcoded secret
    if not _secret_file.exists():
        _secret_file.write_text(secrets.token_urlsafe(48))
    JWT_SECRET = _secret_file.read_text().strip()
JWT_TTL_HOURS = int(os.getenv("BP_JWT_TTL_HOURS", "12"))

# ---- Meta Model API ----
META_API_KEY = os.getenv("MODEL_API_KEY") or os.getenv("META_API_KEY") or ""
META_BASE_URL = os.getenv("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
META_MODEL = os.getenv("META_MODEL", "muse-spark-1.3")
META_ASR_MODEL = os.getenv("META_ASR_MODEL", "muse-voice-transcribe-1.0")
META_REASONING_EFFORT = os.getenv("META_REASONING_EFFORT", "")  # e.g. "low"; empty = API default
META_TIMEOUT_SEC = float(os.getenv("META_TIMEOUT_SEC", "90"))
META_MAX_RETRIES = int(os.getenv("META_MAX_RETRIES", "3"))

# "auto" uses the real API when a key is configured, otherwise the clearly-labelled mock.
AI_MODE_SETTING = os.getenv("BP_AI_MODE", "auto").lower()


def ai_mode() -> str:
    if AI_MODE_SETTING == "mock":
        return "mock"
    if AI_MODE_SETTING == "real":
        return "real"
    return "real" if META_API_KEY else "mock"


AUTOMATION_INTERVAL_SEC = int(os.getenv("BP_AUTOMATION_INTERVAL_SEC", "900"))
RATE_LIMIT_PER_MIN = int(os.getenv("BP_RATE_LIMIT_PER_MIN", "120"))
ASSISTANT_RATE_LIMIT_PER_MIN = int(os.getenv("BP_ASSISTANT_RATE_LIMIT_PER_MIN", "12"))
MAX_UPLOAD_MB = int(os.getenv("BP_MAX_UPLOAD_MB", "15"))
