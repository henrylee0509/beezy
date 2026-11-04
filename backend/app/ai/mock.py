"""Deterministic MOCK mode — used for automated tests, offline development, or when no Meta API key is set.

It routes requests with keyword rules and calls the SAME backend tools as the real agents, then
fills answer templates from the tool results. It is always labelled "Mock mode" in the UI and API;
it never pretends to be Muse Spark.
"""
from __future__ import annotations

import os
import re
import time

from .tools import TOOL_MAP, ToolError, execute, tools_for

ROUTES = [
    ("inventory", r"stock|inventor|restock|running (low|out)|low on|product|beans|milk|syrup|cups|sugar|water"),
    ("procurement", r"supplier|purchase order|\bpo\b|restock|order|cheapest|quote|procure"),
    ("finance", r"cash|money|revenue|expense|afford|invoice|profit|flow|bill|spend|cost|income|sales|financ"),
    ("hr", r"payroll|salar|employee|staff|wage|pay (my|the|all)|people|attendance"),
    ("contracts", r"contract|agreement|lease|renew|expir|clause|notice|rental"),
    ("bi", r"health|priorit|problem|recommend|overview|how is (my|the) business|briefing|afford|focus|summary|biggest|issues"),
]
# Optional pacing for screen recordings so the operations floor is visible (still labelled mock).
STEP_DELAY = int(os.getenv("BP_MOCK_STEP_DELAY_MS", "0")) / 1000

ALL = ["inventory", "procurement", "finance", "hr", "contracts", "bi"]


def plan(text: str) -> dict:
    t = text.lower()
    agents = [k for k, pat in ROUTES if re.search(pat, t)]
    if re.search(r"health check|whole business|complete picture|overall", t):
        agents = ALL[:]
    if not agents:
        agents = ["bi"]
    act_restock = bool(re.search(r"(prepare|draft|create|raise|make|place).{0,30}(purchase order|po|order)|restock (all|the|everything)|go ahead.*restock", t))
    act_payroll = bool(re.search(r"(prepare|run|process|draft).{0,20}payroll", t))
    if act_restock and "procurement" not in agents:
        agents.append("procurement")
    if act_payroll and "hr" not in agents:
        agents.append("hr")
    return {"agents": agents, "restock": act_restock, "payroll": act_payroll}


AGENT_TOOLS = {
    "inventory": ["get_low_stock_products"],
    "procurement": ["get_restock_plan"],
    "finance": ["get_financial_summary", "get_accounts_receivable", "forecast_cash_balance"],
    "hr": ["estimate_payroll"],
    "contracts": ["get_expiring_contracts"],
    "bi": ["evaluate_affordability", "get_recommendations"],
}
NAMES = {"inventory": "Inventory Agent", "procurement": "Procurement Agent", "finance": "Finance Agent", "hr": "HR & Payroll Agent",
         "contracts": "Contract Agent", "bi": "Business Intelligence Agent"}


def _call(run, key, tool_name, args=None):
    tool = TOOL_MAP[tool_name]
    name = NAMES[key]
    if not run.ctx.can(tool.perm):
        run.emit(name, "tool_error", f"{tool_name}: permission denied", {"tool": tool_name})
        return None
    run.emit(name, "tool_call", tool.label or tool_name, {"tool": tool_name, "args": args or {}, "action": tool.action})
    if STEP_DELAY:
        time.sleep(STEP_DELAY)
    try:
        res = execute(tool, run.ctx, args or {})
    except ToolError as e:
        run.emit(name, "tool_error", f"{tool_name}: {e}", {"tool": tool_name, "error": str(e)})
        return None
    run.record_tool(name, tool_name, args or {}, res)
    run.emit(name, "tool_result", f"{tool_name} ✓", {"tool": tool_name, "action": tool.action})
    return res


def _cur(run):
    from .. import db
    with db.connect() as conn:
        return db.one(conn, "SELECT currency FROM businesses WHERE id=?", (run.ctx.business_id,))["currency"]


def run_mock(run, text: str) -> str:
    p = plan(text)
    agents = [a for a in p["agents"] if tools_for(a, run.ctx)]
    denied = [NAMES[a] for a in p["agents"] if a not in agents]
    run.emit("Executive Assistant", "plan", f"Delegating to {len(agents)} agent(s) (mock planner)",
             {"plan": [{"agent": NAMES[a], "task": "deterministic routing"} for a in agents], "mock": True})
    r: dict = {}
    for key in agents:
        name = NAMES[key]
        run.emit(name, "agent_started", f"{name} started", {"task": "deterministic routing"})
        if STEP_DELAY:
            time.sleep(STEP_DELAY / 2)
        if name not in run.agents_used:
            run.agents_used.append(name)
        for tn in AGENT_TOOLS[key]:
            r[tn] = _call(run, key, tn)
        if key == "procurement" and p["restock"]:
            r["draft"] = _call(run, key, "draft_restock_purchase_orders")
        if key == "hr" and p["payroll"]:
            r["prepare_payroll"] = _call(run, key, "prepare_payroll")
        run.emit(name, "agent_finished", f"{name} finished", {})
    c = _cur(run)
    m = lambda v: f"{c} {v:,.2f}"
    out = ["> **Mock mode**: a deterministic summary of your live data. Set `MODEL_API_KEY` to have Muse Spark write it.", ""]
    said = []
    low = r.get("get_low_stock_products")
    d = r.get("draft")
    plan_ = r.get("get_restock_plan")
    if low and low["count"]:
        worst = min(low["products"], key=lambda x: x["days_of_cover"] if x["days_of_cover"] is not None else 1e9)
        said.append(f"**{low['count']} products need restocking.** {worst['name']} is most urgent, with "
                    f"{worst['days_of_cover']} days of cover left.")
    elif low:
        said.append("**Stock is healthy.** Every product is above the low-stock threshold.")
    if d and d["created"]:
        said.append(f"I drafted **{len(d['created'])} purchase orders** ({m(d['total'])}). They're waiting for your approval below; "
                    "nothing has been sent to suppliers.")
    elif d and not d["created"]:
        said.append("No new orders were needed: " + "; ".join(f"{s_['product']} ({s_['reason']})" for s_ in d["skipped"]) + ".")
    elif plan_ and plan_["estimated_total"]:
        said.append(f"Restocking everything low would cost about {m(plan_['estimated_total'])} (estimate).")
    af = r.get("evaluate_affordability")
    fs = r.get("get_financial_summary")
    if af:
        rem = af["projected_balance_after_commitments"]["value"]
        said.append(f"**{'You can cover' if af['affordable'] else 'Cash is tight for'} the next {af['horizon_days']} days.** After payroll, "
                    f"bills and restocking you'd have {m(rem)} left before new sales (estimate).")
    elif fs:
        said.append(f"Cash is {m(fs['cash_balance'])} (recorded).")
    pe = r.get("estimate_payroll")
    pp = r.get("prepare_payroll")
    if pp:
        said.append(f"Payroll {pp['period']} is prepared (gross {m(pp['total_gross'])}) and waiting for owner approval.")
    elif pe:
        issues = [a for a in pe["anomalies"] if a["severity"] == "HIGH"]
        if issues:
            said.append(f"Payroll on {pe['pay_date']} has {len(issues)} issues to fix first.")
    ex = r.get("get_expiring_contracts")
    if ex and ex["contracts"]:
        n = [x for x in ex["contracts"] if x.get("notice_deadline") and x.get("days_to_notice_deadline") is not None
             and 0 <= x["days_to_notice_deadline"] <= ex["within_days"]]
        if n:
            said.append(f"{n[0]['title']} needs a renewal decision by {n[0]['notice_deadline']}.")
    ar = r.get("get_accounts_receivable")
    if ar and ar["overdue_count"] and not af:
        said.append(f"{ar['overdue_count']} customer invoices ({m(ar['total_overdue'])}) are overdue.")
    if denied:
        said.append(f"_Not available for your role ({run.ctx.role}): {', '.join(denied)}._")
    body = " ".join(said) if said else "I couldn't find relevant data for that request."
    return "\n".join(out + [body])
