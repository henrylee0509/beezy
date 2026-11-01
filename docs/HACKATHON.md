# Built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026)

Everything in this repository was written during the hackathon building period; no pre-existing product code was reused.

New work:

- Multi-agent orchestration on Muse Spark 1.3: Executive Assistant delegating to six specialist agents via parallel tool calls, each a separate tool-calling loop (`backend/app/ai/orchestrator.py`).
- ~30 typed, validated, permission-filtered agent tools over deterministic business services (`backend/app/ai/tools.py`, `backend/app/services/`).
- Figure verifier that traces every number in AI answers to tool outputs.
- Muse Voice Transcribe integration with an in-browser 16 kHz WAV recorder (`frontend/js/voice.js`).
- Contract extraction with Muse Spark (page-cited clauses) plus a rule-based fallback.
- Approval queue with server-enforced spending limits; automation engine; insights detectors; cash-flow and demand forecasting; payroll rules engine.
- Full web app (11 pages), demo data generator, PDF/CSV reports, 53 automated tests including a protocol-level fake of the Meta Model API.

Meta models used: `muse-spark-1.3` (chat completions + function calling), `muse-voice-transcribe-1.0` (`/v1/asr/transcribe`).
