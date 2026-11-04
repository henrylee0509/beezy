# Beezy — your AI operations team for small businesses

**Track:** Build for Your World · **Meta models:** Muse Spark 1.3 (tool-calling agents), Muse Voice Transcribe 1.0 (voice commands) via the Meta Model API

Small business owners juggle stock spreadsheets, supplier emails, payroll calculators, contract folders and bank statements. Beezy replaces that with **six specialist AI agents that act as virtual departments** — Inventory, Procurement, Finance, HR & Payroll, Contracts and Business Intelligence — coordinated by an Executive Assistant. They don't just show data: they detect problems, compare options, **prepare the work** (purchase orders, payroll runs, reminders) and **wait for the owner's approval** before anything commits money.

Ask *"Can I afford to restock and pay everyone next month?"* and you watch the agents work in parallel on real records, get one consolidated answer with every figure traced back to the database, and approve the drafted purchase orders right in the chat.

## What makes it more than a chatbot

| | |
|---|---|
| **Real multi-agent orchestration** | The Executive Assistant (Muse Spark) delegates with `consult_*` tool calls, **in parallel**. Each specialist is its own Muse Spark tool-calling loop over ~30 deterministic backend tools. |
| **Verifiable answers** | A verifier checks every figure in the final answer against the raw tool outputs and shows "N/N figures verified" — invented numbers are flagged in the UI. |
| **Humans approve money** | Agents can only *draft*. Purchase orders and payroll go to an approval queue; spending limits and role rules are enforced server-side, so a prompt can't talk its way past them. |
| **Deterministic numbers** | Payroll, totals, thresholds and forecasts are calculated in code; the model explains and decides, never does arithmetic on money. |
| **Role-aware AI** | The tools an agent receives are filtered by the signed-in user's role (staff can't get salary data through the assistant). |
| **Live operations floor** | Every model call, tool call, action and verification is persisted as an event and streamed to the UI — real activity, not an animation. |
| **Proactive automation** | Rules watch stock (the 20% rule), invoices, bills, contracts, payroll, budgets and deliveries; they run on a schedule, on stock-change events, or on demand. |
| **Voice** | Hold the mic: the browser records 16 kHz WAV, Muse Voice Transcribe turns it into text, the agents take it from there. |

## Quick start (about 2 minutes)

Requires Python 3.11 or newer.

```bash
git clone https://github.com/henrylee0509/beezy.git && cd beezy
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # Windows: copy .env.example .env
python run.py                                            # open http://localhost:8000
```

To use live Muse Spark, open `.env` and set your Meta Model API key, then restart:

```bash
MODEL_API_KEY=your-meta-model-api-key
```

Sign in with any demo account. Every account uses the password `demo1234`, and the login page has one-click buttons for each.

| Email | Role | What it can do |
|---|---|---|
| `owner@brewbean.demo` | Owner | Everything, including approving large orders and payroll |
| `manager@brewbean.demo` | Manager | Operations; approves orders up to SGD 2,000; no salary data |
| `hr@brewbean.demo` | HR | Employees, attendance and payroll preparation |
| `finance@brewbean.demo` | Finance | Money, invoices and reports; no salary data |
| `staff@brewbean.demo` | Staff | Restricted view |

The demo business **Brew & Bean Cafe** is seeded automatically on first start, with dates relative to today. The owner can restore it any time from Settings → Reset demo data.

Docker: `docker compose up --build`, then open http://localhost:8000 (it reads `.env` if present).

Without `MODEL_API_KEY` the app runs in **mock mode**: a deterministic planner calls the same tools and templates the answer. Mock mode is labelled everywhere in the UI and API and never claims to be Muse Spark.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest backend/tests -q      # 56 tests
```

They cover thresholds, forecasting, supplier choice, MOQ rounding, spending limits, approvals, payroll maths and workflow, cash forecasting, contract extraction and reminders, automations, tenant isolation, RBAC, validation, report exports, and the **real Muse Spark code path** end-to-end against a protocol-level fake of the Meta Model API (`backend/tests/fake_meta.py`): parallel tool calls, `tool_call_id` round-trips, the verifier catching an invented number, transcription, and graceful fallback when the API is down.

## Stack

- **Backend:** Python 3.11+, Starlette + Pydantic + Uvicorn, SQLite (WAL) through a small tenant-scoped data-access layer, PyJWT, PBKDF2 password hashing.
- **AI:** Meta Model API over HTTPS — `POST /v1/chat/completions` (Muse Spark, OpenAI-compatible tool calling) and `POST /v1/asr/transcribe` (Muse Voice Transcribe). Timeouts, retries with backoff on 429/5xx, and every request logged to `ai_requests`.
- **Frontend:** a no-build single-page app (vanilla ES modules), custom design system, hand-rolled SVG charts. No CDN is needed at runtime (Google Fonts are optional).
- **Exports:** ReportLab PDFs, CSV.

> The original brief suggested Vue + FastAPI + PostgreSQL. This build deliberately uses a zero-build frontend and SQLite so it runs from a fresh clone with one `pip install`; all SQL lives in `backend/app/services/*` behind `db.py`, so moving to PostgreSQL is a contained change.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system & agent architecture, data model (ERD), security model
- [`docs/API.md`](docs/API.md) — REST endpoints
- [`docs/DEMO.md`](docs/DEMO.md) — 3-minute demo script for judges
- [`docs/HACKATHON.md`](docs/HACKATHON.md) — what was built during the hackathon

## Disclaimers

Beezy summarises; it is not certified accounting, tax, legal or investment advice. Payroll rules ship as *unverified* until confirmed against official sources. Supplier submission and salary payments are **simulated** — no real orders or money movements are made. All demo people and companies are fictional.
