"""Executive orchestrator + specialised agents, powered by Muse Spark tool calling.

Flow (real mode):
  1. The Executive Assistant (Muse Spark) reads the request and decides which specialist agents to consult
     by calling consult_<agent> tools — in parallel when independent.
  2. Each specialist is its own Muse Spark tool-calling loop restricted to its domain tools (and to the
     tools the signed-in user's role permits). Tools are deterministic backend functions.
  3. Specialists return findings; the orchestrator writes the consolidated answer.
  4. A verifier checks every figure in the final answer against the raw tool outputs.
Every step is persisted as an agent_event so the UI shows real activity, not a fake animation.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor


from .. import config, db
from ..services.common import iso, today
from . import meta_client, mock
from .tools import TOOL_MAP, ToolError, execute, tools_for

AGENTS = {
    "inventory": {"name": "Inventory Agent", "focus": "stock levels, the low-stock threshold rule, demand forecasts and stockout risk"},
    "procurement": {"name": "Procurement Agent", "focus": "suppliers, quote comparison, restock plans and purchase orders"},
    "finance": {"name": "Finance Agent", "focus": "revenue, expenses, cash balance, receivables, payables, obligations and cash-flow forecasts"},
    "hr": {"name": "HR & Payroll Agent", "focus": "employees, attendance, deterministic payroll estimates, payroll anomalies and payroll preparation"},
    "contracts": {"name": "Contract Agent", "focus": "contracts, expiry and notice deadlines, clauses and page-cited answers"},
    "bi": {"name": "Business Intelligence Agent", "focus": "cross-department health checks, affordability checks and prioritised recommendations"},
}

MAX_ORCH_ROUNDS = 4
MAX_AGENT_ROUNDS = 6
_running: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- run context
class Run:
    def __init__(self, ctx, session_id: int, run_id: str):
        self.ctx = ctx
        self.session_id = session_id
        self.run_id = run_id
        self.tool_outputs: list = []
        self.actions: list[dict] = []
        self.agents_used: list[str] = []
        self.mode = config.ai_mode()
        self.model_calls = 0
        self.tokens = 0
        self._lock = threading.Lock()

    def emit(self, agent: str, kind: str, title: str, data: dict | None = None) -> None:
        with db.connect() as conn:
            db.insert(conn, "agent_events", {"session_id": self.session_id, "run_id": self.run_id, "agent": agent, "kind": kind,
                                             "title": title, "data": json.dumps(data or {}, default=str)})

    def record_tool(self, agent: str, tool_name: str, args: dict, result) -> None:
        with self._lock:
            self.tool_outputs.append({"agent": agent, "tool": tool_name, "result": result})
            tool = TOOL_MAP.get(tool_name)
            if tool and tool.action and isinstance(result, dict):
                self.actions.extend(_extract_actions(tool_name, result))


def _extract_actions(tool_name: str, result: dict) -> list[dict]:
    out = []
    if tool_name == "draft_restock_purchase_orders":
        for c in result.get("created", []):
            out.append({"type": "PURCHASE_ORDER", "title": f"{c['po_number']} · {c['supplier']}", "amount": c["total"],
                        "status": c["status"], "approval_id": c.get("approval_id"), "ref_id": c["po_id"],
                        "detail": ", ".join(c.get("items", [])), "link": "#/procurement"})
    elif tool_name == "create_purchase_order":
        out.append({"type": "PURCHASE_ORDER", "title": f"{result['po_number']} · {result['supplier']}", "amount": result["total"],
                    "status": result["status"], "approval_id": result.get("approval_id"), "ref_id": result["po_id"],
                    "detail": ", ".join(result.get("items", [])), "link": "#/procurement"})
    elif tool_name == "prepare_payroll":
        out.append({"type": "PAYROLL", "title": f"Payroll {result['period']}", "amount": result["total_gross"],
                    "status": result["status"], "approval_id": result.get("approval_id"), "ref_id": result["payroll_run_id"],
                    "detail": f"Net {result['total_net']:,.2f} · employer {result['total_employer_cost']:,.2f}"
                              + (f" · {sum(1 for a in result.get('anomalies', []) if a.get('severity') == 'HIGH')} issue(s)" if result.get("anomalies") else ""),
                    "link": "#/payroll"})
    elif tool_name == "record_transaction":
        out.append({"type": "TRANSACTION", "title": f"Recorded {result['category']} transaction", "amount": result["amount"],
                    "status": "RECORDED", "approval_id": None, "ref_id": result["transaction_id"], "link": "#/finance"})
    return out


def _compact(obj, limit: int = 5000) -> str:
    s = json.dumps(obj, default=str, separators=(",", ":"))
    return s if len(s) <= limit else s[:limit] + '…"(truncated)"'


# ---------------------------------------------------------------- prompts
def _business_context(ctx) -> str:
    with db.connect() as conn:
        b = db.one(conn, "SELECT name, business_type, currency FROM businesses WHERE id=?", (ctx.business_id,))
    return (f"Business: {b['name']} ({b['business_type']}). Currency: {b['currency']}. Today: {iso(today())}. "
            f"Signed-in user: {ctx.name}, role {ctx.role}.")


GROUNDING_RULES = """Rules:
- Every figure you state must come from a tool result. Never invent, estimate or do your own arithmetic on money; if a total is needed, use a tool that computes it (e.g. evaluate_affordability, get_restock_plan, estimate_payroll).
- Clearly label recorded figures versus estimates/projections.
- Never claim an action was executed when it was only drafted. Purchase orders and payroll go to an approval queue; supplier submission and salary payments only happen after a human approves (and are simulated in this MVP).
- If a tool returns a permission error, say what you could not access.
- You are not a lawyer, accountant or financial adviser; summarise, do not give definitive legal, tax or investment advice."""


def _orchestrator_prompt(ctx, available: list[str]) -> str:
    agents = "\n".join(f"- consult_{k}: {AGENTS[k]['name']} — {AGENTS[k]['focus']}" for k in available)
    return f"""You are Beezy, the Executive Assistant that coordinates a team of specialist AI agents acting as virtual departments for a small business owner.
{_business_context(ctx)}

Specialists you can consult (call the consult tools; call several in the same turn when their work is independent):
{agents}

How to work:
1. Decide which specialists are actually needed — do not consult agents that are irrelevant to the request.
2. Give each specialist a precise task. If the owner asks you to DO something (restock, prepare payroll, draft orders), tell the relevant specialist to perform that action.
3. For questions about affordability or overall priorities, consult the Business Intelligence Agent (it has evaluate_affordability and the health snapshot), plus domain agents as needed.
4. Then write the final answer for the owner. The app shows the tool data as visual cards under your answer (stock gauges, purchase orders with approve buttons, cash runway, payroll, contract deadlines, priorities), so do NOT repeat tables or long lists of figures. Write a short summary (under ~120 words): the direct answer first, the one or two figures that matter most (labelled recorded or estimated), then up to three prioritised next steps. Mention that drafted orders or payroll are waiting for approval if any were prepared.
{GROUNDING_RULES}"""


def _agent_prompt(key: str, ctx) -> str:
    a = AGENTS[key]
    return f"""You are the {a['name']} in Beezy, responsible for {a['focus']}.
{_business_context(ctx)}
Use your tools to complete the task from the Executive Assistant. Call tools as needed (several at once if independent), then reply with concise findings: the exact figures from tool results, what they mean, any risks, and any actions you performed (with PO numbers / approval status).
Only perform ACTION tools when the task explicitly asks you to act.
{GROUNDING_RULES}"""


def _consult_schema(key: str) -> dict:
    return {"type": "function", "function": {
        "name": f"consult_{key}",
        "description": f"Delegate a task to the {AGENTS[key]['name']} ({AGENTS[key]['focus']}). Returns its findings and tool data.",
        "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "Specific instructions for the specialist"}},
                       "required": ["task"], "additionalProperties": False}}}


# ---------------------------------------------------------------- real-mode loops
def _run_tool_calls(run: Run, agent_key: str, tool_calls: list[dict]) -> list[dict]:
    agent_name = AGENTS[agent_key]["name"]

    def one(tc):
        name = tc["function"]["name"]
        raw = tc["function"].get("arguments") or "{}"
        tool = TOOL_MAP.get(name)
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = {}
        if not tool or tool.agent != agent_key:
            content = {"error": f"Unknown tool {name}"}
            run.emit(agent_name, "tool_error", f"Unknown tool {name}", {"tool": name})
        else:
            run.emit(agent_name, "tool_call", tool.label or name, {"tool": name, "args": args, "action": tool.action})
            t0 = time.monotonic()
            try:
                result = execute(tool, run.ctx, args)
                run.record_tool(agent_name, name, args, result)
                run.emit(agent_name, "tool_result", f"{name} ✓", {"tool": name, "ms": int((time.monotonic() - t0) * 1000),
                                                                   "preview": _compact(result, 1200), "action": tool.action})
                content = result
            except ToolError as e:
                run.emit(agent_name, "tool_error", f"{name}: {e}", {"tool": name, "error": str(e)})
                content = {"error": str(e)}
        return {"role": "tool", "tool_call_id": tc["id"], "content": _compact(content, 8000)}

    if len(tool_calls) == 1:
        return [one(tool_calls[0])]
    with ThreadPoolExecutor(max_workers=min(6, len(tool_calls))) as ex:
        return list(ex.map(one, tool_calls))


def _assistant_msg(msg: dict) -> dict:
    out = {"role": "assistant", "content": msg.get("content")}  # echo the assistant turn as returned (content may be null)
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def run_agent(run: Run, key: str, task: str) -> dict:
    name = AGENTS[key]["name"]
    tools = tools_for(key, run.ctx)
    if not tools:
        run.emit(name, "agent_error", f"{name} unavailable for role {run.ctx.role}", {})
        return {"agent": name, "findings": f"The signed-in role ({run.ctx.role}) has no access to {name} tools."}
    run.emit(name, "agent_started", f"{name} started", {"task": task, "tools": [t.name for t in tools]})
    with run._lock:
        if name not in run.agents_used:
            run.agents_used.append(name)
    messages = [{"role": "system", "content": _agent_prompt(key, run.ctx)}, {"role": "user", "content": task}]
    schemas = [t.schema() for t in tools]
    used = []
    for rnd in range(MAX_AGENT_ROUNDS):
        run.emit(name, "thinking", "Thinking with Muse Spark" if rnd == 0 else "Reading tool results with Muse Spark", {"round": rnd + 1})
        resp = meta_client.chat(messages, schemas, biz=run.ctx.business_id, run_id=run.run_id, agent=name)
        run.model_calls += 1
        run.tokens += (resp["usage"] or {}).get("total_tokens", 0) or 0
        msg = resp["message"]
        tcs = msg.get("tool_calls") or []
        run.emit(name, "model_call", "Muse Spark reasoning", {"latency_ms": resp["latency_ms"], "model": resp["model"],
                                                               "tool_calls": [tc["function"]["name"] for tc in tcs]})
        if not tcs:
            findings = (msg.get("content") or "").strip()
            run.emit(name, "agent_finished", f"{name} finished", {"findings": findings[:1500], "tools_used": used})
            return {"agent": name, "findings": findings, "tools_used": used}
        messages.append(_assistant_msg(msg))
        used += [tc["function"]["name"] for tc in tcs]
        messages.extend(_run_tool_calls(run, key, tcs))
    messages.append({"role": "user", "content": "Summarise your findings now without calling more tools."})
    resp = meta_client.chat(messages, None, biz=run.ctx.business_id, run_id=run.run_id, agent=name)
    findings = (resp["message"].get("content") or "").strip()
    run.emit(name, "agent_finished", f"{name} finished", {"findings": findings[:1500], "tools_used": used})
    return {"agent": name, "findings": findings, "tools_used": used}


def run_real(run: Run, user_text: str, history: list[dict]) -> str:
    available = [k for k in AGENTS if tools_for(k, run.ctx)]
    messages = [{"role": "system", "content": _orchestrator_prompt(run.ctx, available)}]
    messages += history[-8:]
    messages.append({"role": "user", "content": user_text})
    schemas = [_consult_schema(k) for k in available]
    for rnd in range(MAX_ORCH_ROUNDS):
        run.emit("Executive Assistant", "thinking",
                 "Planning which departments to involve" if rnd == 0 else "Writing your answer from the findings", {"round": rnd + 1})
        resp = meta_client.chat(messages, schemas, biz=run.ctx.business_id, run_id=run.run_id, agent="Executive Assistant")
        run.model_calls += 1
        run.tokens += (resp["usage"] or {}).get("total_tokens", 0) or 0
        msg = resp["message"]
        tcs = msg.get("tool_calls") or []
        if not tcs:
            run.emit("Executive Assistant", "model_call", "Writing consolidated answer", {"latency_ms": resp["latency_ms"], "model": resp["model"]})
            return (msg.get("content") or "").strip()
        plan = []
        for tc in tcs:
            key = tc["function"]["name"].replace("consult_", "")
            try:
                task = json.loads(tc["function"].get("arguments") or "{}").get("task", "")
            except ValueError:
                task = ""
            plan.append({"agent": AGENTS.get(key, {}).get("name", key), "task": task})
        run.emit("Executive Assistant", "plan", f"Delegating to {len(plan)} agent(s)" + (" in parallel" if len(plan) > 1 else ""),
                 {"round": rnd + 1, "plan": plan, "latency_ms": resp["latency_ms"], "model": resp["model"]})
        messages.append(_assistant_msg(msg))

        def consult(tc):
            key = tc["function"]["name"].replace("consult_", "")
            try:
                task = json.loads(tc["function"].get("arguments") or "{}").get("task") or user_text
            except ValueError:
                task = user_text
            if key not in AGENTS or key not in available:
                return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"error": f"Unknown or unavailable agent {key}"})}
            try:
                res = run_agent(run, key, task)
            except meta_client.MetaAPIError:
                raise
            except Exception as e:
                traceback.print_exc()
                res = {"agent": AGENTS[key]["name"], "error": str(e)}
            data = [o for o in run.tool_outputs if o["agent"] == AGENTS[key]["name"]][-4:]
            res["tool_data"] = [{"tool": d["tool"], "result": d["result"]} for d in data]
            return {"role": "tool", "tool_call_id": tc["id"], "content": _compact(res, 14000)}

        with ThreadPoolExecutor(max_workers=min(6, len(tcs))) as ex:
            messages.extend(list(ex.map(consult, tcs)))
    messages.append({"role": "user", "content": "Write the final consolidated answer now."})
    resp = meta_client.chat(messages, None, biz=run.ctx.business_id, run_id=run.run_id, agent="Executive Assistant")
    return (resp["message"].get("content") or "").strip()


# ---------------------------------------------------------------- verification
_NUM = re.compile(r"(?<![\w.])-?(?:[A-Z]{3}\s?|\$)?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?%)?")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{4}-\d{2}\b")


def _collect_numbers(obj, out: set):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.finditer(_DATE.sub(" ", obj)):
            try:
                out.add(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def verify(text: str, tool_outputs: list) -> dict:
    """Check that each significant figure in the answer appears in (or rounds from) a tool result."""
    known: set = set()
    _collect_numbers([o["result"] for o in tool_outputs], known)
    checked, unverified = 0, []
    clean = _DATE.sub(" ", re.sub(r"`[^`]*`", " ", text or ""))
    clean = re.sub(r"\b(PO|INV|HR|MD|HW|E)-?[\w-]*\d+\b", " ", clean)  # identifiers, not figures
    for m in _NUM.finditer(clean):
        raw = m.group(0).strip()
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        is_pct = bool(m.group(2))
        if not is_pct and "." not in m.group(1) and n <= 31:
            continue  # small counts / days: too ambiguous to verify reliably
        if 1990 <= n <= 2100 and "." not in m.group(1) and "," not in m.group(1):
            continue  # years
        checked += 1
        tol = 0.6 if is_pct else (1.0 if n >= 100 else 0.051)
        if not any(abs(abs(k) - n) <= tol for k in known):
            unverified.append(raw)
    return {"figures_checked": checked, "figures_verified": checked - len(unverified),
            "unverified": sorted(set(unverified))[:12],
            "status": "verified" if checked and not unverified else ("no_figures" if not checked else "partial")}


# ---------------------------------------------------------------- entry points
def start(ctx, session_id: int, user_text: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        _running[run_id] = {"status": "running", "session_id": session_id, "started": time.time()}
    th = threading.Thread(target=_execute, args=(ctx, session_id, run_id, user_text), daemon=True)
    th.start()
    return run_id


def run_status(run_id: str) -> dict | None:
    return _running.get(run_id)


def _history(session_id: int, exclude_last: bool = True) -> list[dict]:
    with db.connect() as conn:
        rows = db.all_(conn, "SELECT role, content FROM agent_messages WHERE session_id=? ORDER BY id", (session_id,))
    if exclude_last and rows and rows[-1]["role"] == "user":
        rows = rows[:-1]
    return [{"role": r["role"], "content": r["content"] or ""} for r in rows if r["role"] in ("user", "assistant")]


def _execute(ctx, session_id: int, run_id: str, user_text: str) -> None:
    run = Run(ctx, session_id, run_id)
    t0 = time.monotonic()
    fallback_reason = None
    run.emit("Executive Assistant", "run_started", "Understanding your request",
             {"mode": run.mode, "model": config.META_MODEL if run.mode == "real" else "deterministic-mock"})
    try:
        if run.mode == "real":
            try:
                answer = run_real(run, user_text, _history(session_id))
            except meta_client.MetaAPIError as e:
                fallback_reason = str(e)
                run.emit("Executive Assistant", "error", "Muse Spark unavailable — using deterministic fallback", {"error": str(e)[:400]})
                run.mode = "fallback"
                answer = mock.run_mock(run, user_text)
        else:
            answer = mock.run_mock(run, user_text)
        if not answer:
            answer = "I couldn't produce an answer for that request."
        ver = verify(answer, run.tool_outputs)
        run.emit("Verifier", "verification", f"{ver['figures_verified']}/{ver['figures_checked']} figures traced to tool results", ver)
        from .cards import build_cards
        with db.connect() as conn:
            currency = db.one(conn, "SELECT currency FROM businesses WHERE id=?", (ctx.business_id,))["currency"]
        cards = build_cards(run.tool_outputs, currency)
        meta = {"cards": cards, "run_id": run_id, "mode": run.mode, "model": config.META_MODEL if run.mode == "real" else "deterministic-mock",
                "agents": run.agents_used, "actions": run.actions, "verification": ver, "model_calls": run.model_calls,
                "tokens": run.tokens, "duration_ms": int((time.monotonic() - t0) * 1000), "fallback_reason": fallback_reason}
        with db.connect() as conn:
            mid = db.insert(conn, "agent_messages", {"session_id": session_id, "role": "assistant", "content": answer,
                                                     "meta": json.dumps(meta, default=str)})
            from ..services.common import audit
            audit(conn, ctx.business_id, "AGENT", "Executive Assistant", "assistant.answer", "agent_session", session_id,
                  {"run_id": run_id, "agents": run.agents_used, "actions": len(run.actions), "mode": run.mode})
        run.emit("Executive Assistant", "final", "Answer ready", {"message_id": mid, **meta})
        status = "done"
    except Exception as e:
        traceback.print_exc()
        run.emit("Executive Assistant", "error", "Something went wrong", {"error": str(e)[:500]})
        with db.connect() as conn:
            mid = db.insert(conn, "agent_messages", {"session_id": session_id, "role": "assistant",
                                                     "content": f"Sorry — the request failed: {e}",
                                                     "meta": json.dumps({"run_id": run_id, "error": True})})
        run.emit("Executive Assistant", "final", "Failed", {"message_id": mid, "error": True})
        status = "error"
    with _lock:
        _running[run_id] = {"status": status, "session_id": session_id}


# ---------------------------------------------------------------- single-shot helpers
def complete_json(system: str, user: str, *, biz: int, agent: str) -> dict:
    resp = meta_client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], None, biz=biz, agent=agent)
    return meta_client.parse_json(resp["message"].get("content") or "")


def complete_text(system: str, user: str, *, biz: int, agent: str) -> str:
    resp = meta_client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], None, biz=biz, agent=agent)
    return (resp["message"].get("content") or "").strip()


