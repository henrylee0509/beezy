"""Deterministic MOCK mode — used for automated tests, offline development, or when no Meta API key is set.

It routes requests with keyword rules and calls the SAME backend tools as the real agents, then
fills answer templates from the tool results. It is always labelled "Mock mode" in the UI and API;
it never pretends to be Muse Spark.
"""
from __future__ import annotations

import re

from .tools import TOOL_MAP, ToolError, execute, tools_for

ROUTES = [
    ("inventory", r"stock|inventor|restock|running (low|out)|low on|product|beans|milk|syrup|cups|sugar|water"),
    ("procurement", r"supplier|purchase order|\bpo\b|restock|order|cheapest|quote|procure"),
    ("finance", r"cash|money|revenue|expense|afford|invoice|profit|flow|bill|spend|cost|income|sales|financ"),
    ("hr", r"payroll|salar|employee|staff|wage|pay (my|the|all)|people|attendance"),
    ("contracts", r"contract|agreement|lease|renew|expir|clause|notice|rental"),
    ("bi", r"health|priorit|problem|recommend|overview|how is (my|the) business|briefing|afford|focus|summary|biggest|issues"),
]
ALL = ["inventory", "procurement", "finance", "hr", "contracts", "bi"]


def plan(text: str) -> dict:
    t = text.lower()
    agents = [k for k, pat in ROUTES if re.search(pat, t)]
    if re.search(r"health check|everything|whole business|complete|overall", t):
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
    out = ["> **Mock mode** — deterministic answer built from live business data. Set `MODEL_API_KEY` to use Muse Spark.", ""]
    lines = []
    low = r.get("get_low_stock_products")
    if low:
        if low["count"]:
            lines.append("**Inventory** — " + "; ".join(f"{x['name']} {x['stock_pct']}% ({x['days_of_cover']} days of cover)" for x in low["products"]))
        else:
            lines.append("**Inventory** — all products are above the low-stock threshold.")
    plan_ = r.get("get_restock_plan")
    if plan_ and plan_["items"]:
        todo = [i for i in plan_["items"] if i.get("supplier") and not i.get("already_on_order")]
        on_order = [f"{i['product']} ({i['already_on_order']})" for i in plan_["items"] if i.get("already_on_order")]
        if todo:
            lines.append(f"**Restock plan** — estimated {c} {plan_['estimated_total']:,.2f}: " + "; ".join(
                f"{i['quantity']:g} × {i['product']} from {i['supplier']} ({c} {i['line_total']:,.2f})" for i in todo))
        if on_order:
            lines.append("**Already on order** — " + ", ".join(on_order) + ".")
    fs = r.get("get_financial_summary")
    if fs:
        lines.append(f"**Finance ({fs['period_label']})** — revenue {c} {fs['revenue']:,.2f}, expenses {c} {fs['expenses']:,.2f}, "
                     f"net operating profit {c} {fs['net_operating_profit']:,.2f}; cash balance {c} {fs['cash_balance']:,.2f} (recorded).")
    ar = r.get("get_accounts_receivable")
    if ar and ar["overdue_count"]:
        lines.append(f"**Receivables** — {ar['overdue_count']} overdue invoice(s) totalling {c} {ar['total_overdue']:,.2f}.")
    fc = r.get("forecast_cash_balance")
    if fc:
        lines.append(f"**30-day cash forecast** — committed scenario ends at {c} {fc['projected_committed_balance']:,.2f} "
                     f"(lowest {c} {fc['lowest_committed_balance']:,.2f}); with estimated sales {c} {fc['projected_expected_balance']:,.2f} (estimate).")
    pe = r.get("estimate_payroll")
    if pe:
        issues = [a for a in pe["anomalies"] if a["severity"] == "HIGH"]
        lines.append(f"**Payroll {pe['period']}** (pay date {pe['pay_date']}) — gross {c} {pe['total_gross']:,.2f}, "
                     f"total cost {c} {pe['total_cost_to_business']:,.2f} (deterministic estimate)"
                     + (f"; {len(issues)} issue(s): " + "; ".join(f"{a['employee']}: {a['issue']}" for a in issues) if issues else "."))
    ex = r.get("get_expiring_contracts")
    if ex and ex["contracts"]:
        lines.append("**Contracts** — " + "; ".join(
            f"{x['title']} " + (f"(notice deadline {x['notice_deadline']})" if x.get("notice_deadline") and x["days_to_notice_deadline"] is not None
                                and x["days_to_notice_deadline"] <= ex["within_days"] else f"(expires {x['end_date']})")
            for x in ex["contracts"]))
    af = r.get("evaluate_affordability")
    if af:
        rem = af["projected_balance_after_commitments"]["value"]
        lines.append(f"**Affordability (next {af['horizon_days']} days)** — cash {c} {af['current_cash_balance']['value']:,.2f} vs committed outflows "
                     f"{c} {af['total_committed_outflows']:,.2f} (payroll {c} {af['payroll_due']['value']:,.2f}, new restock {c} "
                     f"{af['recommended_restock_cost']['value']:,.2f}, open orders {c} {af['open_purchase_orders_due']['value']:,.2f}); "
                     f"projected balance {c} {rem:,.2f} before new sales → "
                     + ("**affordable**." if af["affordable"] else "**shortfall** — phase purchases and collect receivables."))
    recs = r.get("get_recommendations")
    if recs and recs["recommendations"]:
        lines.append("**Top priorities**\n" + "\n".join(f"{i}. {x['title']} — {x['suggested_action']}"
                                                         for i, x in enumerate(recs["recommendations"][:3], 1)))
    d = r.get("draft")
    pp = r.get("prepare_payroll")
    if (d and d["created"]) or pp:
        acts = []
        if d:
            acts += [f"{x['po_number']} · {x['supplier']} · {c} {x['total']:,.2f} → {x['status'].replace('_', ' ').lower()}" for x in d["created"]]
        if pp:
            acts.append(f"Payroll {pp['period']} draft (gross {c} {pp['total_gross']:,.2f}) → awaiting owner approval")
        lines.append("**Proposed actions awaiting your approval**\n" + "\n".join(f"- {a}" for a in acts))
    elif d and not d["created"]:
        lines.append("No new purchase orders were needed: " + "; ".join(f"{s['product']} ({s['reason']})" for s in d["skipped"]))
    if denied:
        lines.append(f"_Not available for your role ({run.ctx.role}): {', '.join(denied)}._")
    return "\n".join(out + ["\n\n".join(lines) if lines else "I couldn't find relevant data for that request."])
