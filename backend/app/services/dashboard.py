"""Consolidated overview: KPIs, chart data, alerts and the fact bundle for the AI briefing."""
from __future__ import annotations

from datetime import timedelta

from .. import db
from . import approvals, contracts, finance, insights, inventory, payroll, procurement
from .common import add_months, iso, money, month_bounds, month_key, today


def overview(conn, biz: int, include_finance: bool = True, include_salary: bool = True) -> dict:
    t = today()
    b = db.one(conn, "SELECT * FROM businesses WHERE id=?", (biz,))
    products = inventory.list_products(conn, biz)
    sm = finance.summary(conn, biz)
    pending_pos = db.scalar(conn, """SELECT COUNT(*) FROM purchase_orders WHERE business_id=?
                                     AND status IN ('DRAFT','AWAITING_APPROVAL','APPROVED','SUBMITTED','PARTIALLY_RECEIVED')""", (biz,))
    emp_count = db.scalar(conn, "SELECT COUNT(*) FROM employees WHERE business_id=? AND status='ACTIVE'", (biz,))
    kpis = {
        "inventory_value": inventory.inventory_value(conn, biz),
        "active_employees": emp_count,
        "pending_purchase_orders": pending_pos,
        "low_stock_count": sum(1 for p in products if p["level"] != "HEALTHY"),
        "pending_approvals": len(approvals.list_(conn, biz)),
    }
    if include_finance:
        kpis.update({k: sm[k] for k in ("revenue", "expenses", "net_operating_profit", "cash_balance",
                                         "accounts_receivable", "overdue_receivables", "accounts_payable",
                                         "revenue_change_pct", "expenses_change_pct", "overdue_invoice_count")})
        kpis["outstanding_invoices"] = db.scalar(conn, "SELECT COUNT(*) FROM invoices WHERE business_id=? AND kind='RECEIVABLE' AND status='OPEN'", (biz,))
    charts = {
        "inventory_levels": [{"name": p["name"], "stock_pct": p["stock_pct"], "level": p["level"]} for p in products],
        "inventory_by_category": _inventory_by_category(products),
        "top_products": _top_products(conn, biz),
    }
    if include_finance:
        charts.update({
            "revenue_vs_expenses": finance.monthly_series(conn, biz, 6),
            "cash_trend": finance.cash_trend(conn, biz, 60),
            "expense_breakdown": finance.category_breakdown(conn, biz, "EXPENSE", t - timedelta(days=29), t),
            "daily_sales": _daily_sales(conn, biz, 30),
            "payroll_monthly": _payroll_monthly(conn, biz),
        })
    return {"business": b, "as_of": iso(t), "kpis": kpis, "charts": charts,
            "alerts": alerts(conn, biz, include_finance, include_salary),
            "definitions": finance.METRIC_DEFINITIONS}


def _inventory_by_category(products):
    agg = {}
    for p in products:
        agg[p["category"]] = agg.get(p["category"], 0) + p["stock_value"]
    return [{"category": k, "value": money(v)} for k, v in sorted(agg.items(), key=lambda x: -x[1])]


def _top_products(conn, biz):
    """Stock consumed in the last 30 days, valued at cost."""
    t = today()
    return db.all_(conn, """SELECT p.name, ROUND(SUM(-m.quantity),1) AS units, ROUND(SUM(-m.quantity)*p.unit_cost,2) AS consumption_value
                            FROM inventory_movements m JOIN products p ON p.id=m.product_id
                            WHERE m.business_id=? AND m.kind='sale' AND m.occurred_on>? GROUP BY p.id
                            ORDER BY consumption_value DESC LIMIT 7""", (biz, iso(t - timedelta(days=30))))


def _daily_sales(conn, biz, days):
    t = today()
    rows = {r["txn_date"]: r["a"] for r in db.all_(conn, """SELECT txn_date, SUM(amount) AS a FROM transactions
                    WHERE business_id=? AND direction='INCOME' AND category='Sales' AND txn_date>? GROUP BY txn_date""",
                                                       (biz, iso(t - timedelta(days=days))))}
    # Sales are recorded at close of day, so the series ends yesterday.
    return [{"date": iso(t - timedelta(days=i)), "sales": money(rows.get(iso(t - timedelta(days=i)), 0))}
            for i in range(days, 0, -1)]


def _payroll_monthly(conn, biz):
    t = today()
    out = []
    for i in range(5, -1, -1):
        m = month_key(add_months(t.replace(day=1), -i))
        s, e = month_bounds(m)
        v = db.scalar(conn, """SELECT COALESCE(SUM(amount),0) FROM transactions WHERE business_id=? AND direction='EXPENSE'
                               AND category='Salaries' AND txn_date>=? AND txn_date<=?""", (biz, iso(s), iso(e)))
        out.append({"month": m, "salaries": money(v)})
    return out


def alerts(conn, biz: int, include_finance: bool = True, include_salary: bool = True) -> list[dict]:
    out = []
    for a in inventory.open_alerts(conn, biz):
        sev = "critical" if a["level"] in ("CRITICAL", "OUT", "URGENT") else "warning"
        out.append({"type": "inventory", "severity": sev, "title": f"{a['product']} at {a['stock_pct']}%",
                    "detail": f"{a['current_stock']:g}/{a['max_stock']:g} {a['unit']} · {a['level'].title()}", "link": "#/inventory"})
    pay_day = payroll.next_payroll_date(conn, biz)
    days = (pay_day - today()).days
    if days <= 10:
        run = db.one(conn, "SELECT status FROM payroll_runs WHERE business_id=? AND period=? AND status<>'CANCELLED'",
                     (biz, month_key(pay_day)))
        status = run["status"].replace("_", " ").lower() if run else "not prepared"
        detail = f"In {days} days ({iso(pay_day)}) · {status}"
        if include_salary:
            est = payroll.estimate(conn, biz, month_key(pay_day))
            detail += f" · est. {est['total_cost_to_business']:,.2f}"
        out.append({"type": "payroll", "severity": "warning" if not run or run["status"] in ("DRAFT", "AWAITING_APPROVAL") else "info",
                    "title": "Upcoming payroll", "detail": detail, "link": "#/payroll"})
    if include_finance:
        ar = finance.receivables(conn, biz)
        if ar["overdue_count"]:
            out.append({"type": "finance", "severity": "critical" if ar["over_threshold_count"] else "warning",
                        "title": f"{ar['overdue_count']} overdue customer invoice(s)",
                        "detail": f"{ar['total_overdue']:,.2f} outstanding", "link": "#/finance"})
        ap = finance.payables(conn, biz)
        if ap["due_7_days_count"]:
            out.append({"type": "finance", "severity": "warning", "title": f"{ap['due_7_days_count']} supplier bill(s) due within 7 days",
                        "detail": f"{ap['due_7_days']:,.2f} payable", "link": "#/finance"})
        for a in finance.spending_anomalies(conn, biz):
            out.append({"type": "finance", "severity": "warning", "title": f"Unusual {a['category']} spending",
                        "detail": f"+{a['increase_pct']}% vs prior 30-day average", "link": "#/finance"})
    for c in contracts.list_contracts(conn, biz):
        if c["days_to_notice_deadline"] is not None and 0 <= c["days_to_notice_deadline"] <= 30:
            out.append({"type": "contract", "severity": "critical" if c["days_to_notice_deadline"] <= 14 else "warning",
                        "title": f"{c['title']}: notice deadline in {c['days_to_notice_deadline']} days",
                        "detail": f"Auto-renews {c['end_date']}", "link": "#/contracts"})
        elif c["days_to_expiry"] is not None and 0 <= c["days_to_expiry"] <= 60:
            out.append({"type": "contract", "severity": "warning", "title": f"{c['title']} expires in {c['days_to_expiry']} days",
                        "detail": c["counterparty"] or "", "link": "#/contracts"})
    late = [po for po in procurement.list_pos(conn, biz, "SUBMITTED") if po["expected_date"] and po["expected_date"] < iso(today())]
    for po in late:
        out.append({"type": "procurement", "severity": "warning", "title": f"{po['po_number']} delivery is late",
                    "detail": f"{po['supplier']} · expected {po['expected_date']}", "link": "#/procurement"})
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["severity"]])


def briefing_facts(conn, biz: int) -> dict:
    """Verified numbers for the AI briefing. The model may only restate these figures."""
    b = db.one(conn, "SELECT name, currency, business_type FROM businesses WHERE id=?", (biz,))
    sm = finance.summary(conn, biz)
    low = inventory.low_stock_products(conn, biz)
    pay_day = payroll.next_payroll_date(conn, biz)
    est = payroll.estimate(conn, biz, month_key(pay_day))
    ar = finance.receivables(conn, biz)
    ap = finance.payables(conn, biz)
    fc = finance.forecast(conn, biz, 30)
    plan = procurement.restock_plan(conn, biz)
    return {
        "business": b["name"], "currency": b["currency"], "as_of": iso(today()),
        "month_to_date": {"revenue": sm["revenue"], "expenses": sm["expenses"], "net_operating_profit": sm["net_operating_profit"],
                          "revenue_change_pct_vs_same_days_last_month": sm["revenue_change_pct"]},
        "cash_balance": sm["cash_balance"],
        "low_stock": [{"product": p["name"], "stock_pct": p["stock_pct"], "days_of_cover": p.get("days_of_cover")} for p in low],
        "restock_estimated_cost": plan["estimated_total"],
        "payroll": {"date": iso(pay_day), "estimated_gross": est["total_gross"],
                    "estimated_total_cost": est["total_cost_to_business"],
                    "issues": [a["issue"] for a in est["anomalies"] if a["severity"] == "HIGH"]},
        "receivables": {"overdue_count": ar["overdue_count"], "overdue_total": ar["total_overdue"]},
        "payables_due_7_days": {"count": ap["due_7_days_count"], "total": ap["due_7_days"]},
        "cash_forecast_30_days": {"committed_end_balance": fc["projected_committed_balance"],
                                  "lowest_committed_balance": fc["lowest_committed_balance"],
                                  "expected_end_balance": fc["projected_expected_balance"]},
        "top_recommendations": [{"title": r["title"], "priority": r["priority"]} for r in insights.top(conn, biz, 4)],
    }
