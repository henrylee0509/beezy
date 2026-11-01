"""Automation engine: configurable trigger -> conditions -> actions -> approval, with run history.

Automations can prepare work (alerts, draft POs, draft payroll, reminders) but never bypass approval
policies: anything that commits money goes to the approval queue unless the owner explicitly enabled
auto-approval with a spending limit.
"""
from __future__ import annotations

import json
import traceback
from datetime import timedelta

from .. import db
from ..web import bad_request, not_found
from . import contracts, finance, insights, inventory, payroll, procurement
from .common import audit, iso, money, month_key, now_iso, parse_date, settings, today

TRIGGERS = {
    "STOCK_BELOW_PCT": {"label": "Stock falls below % of capacity", "params": {"pct": 20}},
    "STOCK_CRITICAL": {"label": "Stock reaches critical level", "params": {"pct": 5}},
    "INVOICE_OVERDUE": {"label": "Customer invoice overdue", "params": {"days": 30}},
    "BILL_DUE_SOON": {"label": "Supplier bill approaching due date", "params": {"days": 7}},
    "CONTRACT_EXPIRING": {"label": "Contract approaching expiry / notice deadline", "params": {}},
    "PAYROLL_DUE": {"label": "Payroll date approaching", "params": {"days_before": 5}},
    "EXPENSE_OVER_BUDGET": {"label": "Expense category exceeds budget", "params": {}},
    "REVENUE_BELOW_TARGET": {"label": "Month-to-date revenue below target pace", "params": {"monthly_target": 40000}},
    "DELIVERY_DELAYED": {"label": "Supplier delivery is late", "params": {}},
}
ACTIONS = {
    "ALERT": "Raise an alert", "FORECAST": "Forecast stockout date", "DRAFT_PO": "Draft purchase order(s)",
    "RECOMMEND": "Create a recommendation", "REMINDER": "Create reminders", "PREPARE_PAYROLL": "Prepare draft payroll",
    "DRAFT_REMINDER_EMAIL": "Draft a payment reminder message",
}


def list_rules(conn, biz: int) -> list[dict]:
    rules = db.all_(conn, "SELECT * FROM automation_rules WHERE business_id=? ORDER BY id", (biz,))
    for r in rules:
        r["trigger_label"] = TRIGGERS.get(r["trigger_type"], {}).get("label", r["trigger_type"])
        r["action_labels"] = [ACTIONS.get(a, a) for a in r["actions"]]
        last = db.one(conn, "SELECT status, summary, finished_at FROM automation_runs WHERE rule_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        r["last_run"] = last
    return rules


def _validate(data: dict) -> None:
    if data.get("trigger_type") and data["trigger_type"] not in TRIGGERS:
        raise bad_request("Unknown trigger type")
    for a in data.get("actions") or []:
        if a not in ACTIONS:
            raise bad_request(f"Unknown action {a}")


def create_rule(conn, biz: int, data: dict, actor: str) -> dict:
    _validate(data)
    rid = db.insert(conn, "automation_rules", {"business_id": biz, **data})
    audit(conn, biz, "USER", actor, "automation.create", "automation_rule", rid, data)
    return db.get_owned(conn, "automation_rules", rid, biz)


def update_rule(conn, biz: int, rid: int, data: dict, actor: str) -> dict:
    if not db.get_owned(conn, "automation_rules", rid, biz):
        raise not_found("Automation")
    _validate(data)
    db.update(conn, "automation_rules", rid, data, biz)
    audit(conn, biz, "USER", actor, "automation.update", "automation_rule", rid, data)
    return db.get_owned(conn, "automation_rules", rid, biz)


def toggle(conn, biz: int, rid: int, actor: str) -> dict:
    r = db.get_owned(conn, "automation_rules", rid, biz)
    if not r:
        raise not_found("Automation")
    db.update(conn, "automation_rules", rid, {"enabled": 0 if r["enabled"] else 1}, biz)
    audit(conn, biz, "USER", actor, "automation.toggle", "automation_rule", rid, {"enabled": not r["enabled"]})
    return db.get_owned(conn, "automation_rules", rid, biz)


def history(conn, biz: int, limit: int = 50) -> list[dict]:
    return db.all_(conn, "SELECT * FROM automation_runs WHERE business_id=? ORDER BY id DESC LIMIT ?", (biz, limit))


# ---------------- trigger handlers ----------------
def _h_stock(conn, biz, rule, actor):
    pct = float(rule["params"].get("pct", 20))
    s = settings(conn, biz)
    items = [p for p in inventory.list_products(conn, biz) if p["stock_pct"] < pct]
    if not items:
        return [], "All products above threshold"
    details = []
    new_alerts = inventory.sync_alerts(conn, biz)
    for a in new_alerts:
        details.append({"type": "alert", "text": f"{a['level']} alert: {a['product']} at {a['stock_pct']}%"})
    if "FORECAST" in rule["actions"]:
        for p in items:
            details.append({"type": "forecast", "text": f"{p['name']}: {p['stock_pct']}% · {p.get('days_of_cover')} days of cover"
                                                         f" · stockout ~{p.get('depletion_date') or 'n/a'}"})
    if "DRAFT_PO" in rule["actions"]:
        res = procurement.draft_restock_orders(conn, biz, actor, [p["id"] for p in items], origin="automation")
        for c in res["created"]:
            details.append({"type": "purchase_order", "text": f"{c['po_number']} for {c['supplier']} ({c['total']:.2f}) → {c['status']}",
                            "po_id": c["po_id"]})
        for sk in res["skipped"]:
            details.append({"type": "skipped", "text": f"{sk['product']}: {sk['reason']}"})
    if "ALERT" in rule["actions"] and not new_alerts:
        details.append({"type": "alert", "text": f"{len(items)} product(s) below {pct:g}% (alerts already open)"})
    _ = s
    return details, f"{len(items)} product(s) below {pct:g}%"


def _h_invoice_overdue(conn, biz, rule, actor):
    days = int(rule["params"].get("days", 30))
    rows = [i for i in finance.receivables(conn, biz)["invoices"] if i["days_overdue"] > days]
    details = [{"type": "alert", "text": f"{i['number']} · {i['counterparty']} · {i['amount']:.2f} · {i['days_overdue']} days overdue"}
               for i in rows]
    if rows and "DRAFT_REMINDER_EMAIL" in rule["actions"]:
        for i in rows:
            details.append({"type": "draft", "text": f"Draft reminder for {i['counterparty']}",
                            "message": f"Hi {i['counterparty']}, this is a friendly reminder that invoice {i['number']} for "
                                       f"{i['amount']:.2f} was due on {i['due_date']}. Could you let us know when we can expect "
                                       f"payment? Thank you."})
    return details, f"{len(rows)} invoice(s) over {days} days overdue"


def _h_bill_due(conn, biz, rule, actor):
    days = int(rule["params"].get("days", 7))
    rows = [i for i in finance.payables(conn, biz)["invoices"] if i["days_until_due"] <= days]
    return ([{"type": "alert", "text": f"{i['number']} · {i['counterparty']} · {i['amount']:.2f} due {i['due_date']}"} for i in rows],
            f"{len(rows)} bill(s) due within {days} days")


def _h_contract(conn, biz, rule, actor):
    n = contracts.sync_reminders(conn, biz)
    due = contracts.due_reminders(conn, biz)
    details = [{"type": "reminder", "text": f"{r['title']}: {r['label']} ({r['remind_on']})"} for r in due]
    for r in due:
        db.update(conn, "contract_reminders", r["id"], {"status": "SENT"}, biz)
    return details, f"{len(due)} reminder(s) due, {n} new scheduled"


def _h_payroll(conn, biz, rule, actor):
    days_before = int(rule["params"].get("days_before", 5))
    pay_day = payroll.next_payroll_date(conn, biz)
    if (pay_day - today()).days > days_before:
        return [], f"Next payroll {iso(pay_day)} — outside the {days_before}-day window"
    period = month_key(pay_day)
    existing = db.one(conn, "SELECT status FROM payroll_runs WHERE business_id=? AND period=? AND status<>'CANCELLED'", (biz, period))
    if existing and existing["status"] != "DRAFT":
        return [{"type": "info", "text": f"Payroll {period} already {existing['status'].lower()}"}], "Nothing to do"
    if "PREPARE_PAYROLL" in rule["actions"]:
        run = payroll.prepare(conn, biz, period, actor)
        return [{"type": "payroll", "text": f"Draft payroll {period}: gross {run['total_gross']:.2f}, awaiting owner approval",
                 "run_id": run["id"]}], f"Payroll {period} prepared"
    return [{"type": "alert", "text": f"Payroll due on {iso(pay_day)}"}], "Payroll reminder"


def _h_budget(conn, biz, rule, actor):
    rows = [b for b in finance.budget_status(conn, biz) if b["over"] or (b["used_pct"] or 0) >= 90]
    return ([{"type": "alert", "text": f"{b['category']}: {b['spent']:.2f} of {b['budget']:.2f} budget ({b['used_pct']}%)"} for b in rows],
            f"{len(rows)} budget(s) at or over 90%")


def _h_revenue(conn, biz, rule, actor):
    target = float(rule["params"].get("monthly_target", 0))
    if not target:
        return [], "No target configured"
    sm = finance.summary(conn, biz)
    t = today()
    from calendar import monthrange
    pace = target * t.day / monthrange(t.year, t.month)[1]
    if sm["revenue"] < pace * 0.95:
        return [{"type": "alert", "text": f"Revenue {sm['revenue']:.2f} vs target pace {pace:.2f}"}], "Below target pace"
    return [], f"On pace ({sm['revenue']:.2f} vs {pace:.2f})"


def _h_delivery(conn, biz, rule, actor):
    late = [po for po in procurement.list_pos(conn, biz, "SUBMITTED")
            if po["expected_date"] and parse_date(po["expected_date"]) < today()]
    return ([{"type": "alert", "text": f"{po['po_number']} from {po['supplier']} expected {po['expected_date']}"} for po in late],
            f"{len(late)} late deliver{'y' if len(late) == 1 else 'ies'}")


HANDLERS = {"STOCK_BELOW_PCT": _h_stock, "STOCK_CRITICAL": _h_stock, "INVOICE_OVERDUE": _h_invoice_overdue,
            "BILL_DUE_SOON": _h_bill_due, "CONTRACT_EXPIRING": _h_contract, "PAYROLL_DUE": _h_payroll,
            "EXPENSE_OVER_BUDGET": _h_budget, "REVENUE_BELOW_TARGET": _h_revenue, "DELIVERY_DELAYED": _h_delivery}


def run_rule(conn, biz: int, rule: dict, source: str = "schedule") -> dict:
    actor = f"automation:{rule['name']}"
    run_id = db.insert(conn, "automation_runs", {"business_id": biz, "rule_id": rule["id"], "rule_name": rule["name"],
                                                 "status": "RUNNING"})
    try:
        details, summary = HANDLERS[rule["trigger_type"]](conn, biz, rule, actor)
        status = "TRIGGERED" if details else "NO_ACTION"
    except Exception as e:
        traceback.print_exc()
        details, summary, status = [{"type": "error", "text": str(e)}], "Failed", "FAILED"
    db.update(conn, "automation_runs", run_id, {"status": status, "summary": summary, "details": json.dumps(details, default=str),
                                                "finished_at": now_iso()}, biz)
    db.update(conn, "automation_rules", rule["id"], {"last_run_at": now_iso()}, biz)
    if status == "TRIGGERED":
        audit(conn, biz, "AUTOMATION", actor, "automation.run", "automation_rule", rule["id"],
              {"summary": summary, "source": source, "actions": len(details)})
    return {"run_id": run_id, "rule": rule["name"], "status": status, "summary": summary, "details": details}


def run_all(conn, biz: int, source: str = "schedule", trigger_types: set[str] | None = None) -> list[dict]:
    rules = [r for r in db.all_(conn, "SELECT * FROM automation_rules WHERE business_id=? AND enabled=1 ORDER BY id", (biz,))
             if not trigger_types or r["trigger_type"] in trigger_types]
    out = [run_rule(conn, biz, r, source) for r in rules]
    insights.refresh(conn, biz)
    return out


def run_one(conn, biz: int, rid: int) -> dict:
    r = db.get_owned(conn, "automation_rules", rid, biz)
    if not r:
        raise not_found("Automation")
    res = run_rule(conn, biz, r, "manual")
    insights.refresh(conn, biz)
    return res
