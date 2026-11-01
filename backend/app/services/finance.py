"""Finance: cash position, P&L (cash basis), receivables/payables and cash-flow forecasting.

Definitions used everywhere (shown in the UI):
- Revenue: income transactions recorded in the period.
- Expenses: expense transactions recorded in the period.
- Net operating profit: revenue - expenses for the period (cash basis, before tax).
- Cash balance: opening balance + all PAID income - all PAID expenses to date.
- Accounts receivable / payable: OPEN customer / supplier invoices.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .. import db
from ..web import bad_request, not_found
from .common import (actor_type_for, add_months, audit, iso, money, month_bounds, month_key, parse_date,
                     settings, today)

INCOME_CATEGORIES = ["Sales", "Catering", "Other Income"]
EXPENSE_CATEGORIES = ["Supplier Payments", "Salaries", "Rent", "Utilities", "Marketing", "Equipment",
                      "Taxes & Statutory", "Maintenance", "Other Operating"]

METRIC_DEFINITIONS = {
    "revenue": "Income transactions recorded in the period.",
    "expenses": "Expense transactions recorded in the period.",
    "net_operating_profit": "Revenue minus expenses for the period (cash basis, before tax).",
    "cash_balance": "Opening balance plus all paid income minus all paid expenses to date.",
    "accounts_receivable": "Open (unpaid) customer invoices.",
    "accounts_payable": "Open (unpaid) supplier bills.",
    "cash_inflow": "Paid income received in the period.",
    "cash_outflow": "Paid expenses in the period.",
}

_CATEGORY_KEYWORDS = {
    "Rent": ["rent", "lease"], "Utilities": ["electric", "water bill", "utility", "utilities", "gas", "internet"],
    "Salaries": ["salary", "payroll", "wages"], "Marketing": ["ads", "advert", "marketing", "promo", "instagram", "flyer"],
    "Supplier Payments": ["supplier", "beans", "milk", "dairy", "wholesale", "invoice bill"],
    "Equipment": ["machine", "grinder", "equipment", "fridge"], "Maintenance": ["repair", "maintenance", "cleaning"],
    "Taxes & Statutory": ["tax", "gst", "levy", "statutory"], "Catering": ["catering"],
}


def suggest_category(description: str, direction: str) -> str:
    d = (description or "").lower()
    for cat, words in _CATEGORY_KEYWORDS.items():
        if any(w in d for w in words):
            if direction == "INCOME" and cat not in INCOME_CATEGORIES:
                continue
            if direction == "EXPENSE" and cat in INCOME_CATEGORIES:
                continue
            return cat
    return "Sales" if direction == "INCOME" else "Other Operating"


def cash_balance(conn, biz: int, as_of=None) -> float:
    acct = db.one(conn, "SELECT COALESCE(SUM(opening_balance),0) AS ob, MIN(opening_date) AS od FROM financial_accounts WHERE business_id=?",
                  (biz,))
    as_of = iso(as_of or today())
    od = acct["od"] or "0000-01-01"
    inc = db.scalar(conn, """SELECT COALESCE(SUM(amount),0) FROM transactions WHERE business_id=? AND direction='INCOME'
                             AND status='PAID' AND txn_date>=? AND txn_date<=?""", (biz, od, as_of))
    exp = db.scalar(conn, """SELECT COALESCE(SUM(amount),0) FROM transactions WHERE business_id=? AND direction='EXPENSE'
                             AND status='PAID' AND txn_date>=? AND txn_date<=?""", (biz, od, as_of))
    return money((acct["ob"] or 0) + inc - exp)


def _period_totals(conn, biz: int, start, end) -> dict:
    rows = db.all_(conn, """SELECT direction, COALESCE(SUM(amount),0) AS total FROM transactions
                            WHERE business_id=? AND txn_date>=? AND txn_date<=? GROUP BY direction""",
                   (biz, iso(start), iso(end)))
    d = {r["direction"]: r["total"] for r in rows}
    rev, exp = money(d.get("INCOME", 0)), money(d.get("EXPENSE", 0))
    return {"revenue": rev, "expenses": exp, "net_operating_profit": money(rev - exp)}


def _pct(cur, prev):
    return round((cur - prev) / prev * 100, 1) if prev else None


def summary(conn, biz: int, period: str | None = None) -> dict:
    """period: 'YYYY-MM' (month-to-date for the current month) or 'last30' (rolling 30 days, default)."""
    t = today()
    period = period or "last30"
    if period == "last30":
        start, end = t - timedelta(days=29), t
        prev_start, prev_end = start - timedelta(days=30), start - timedelta(days=1)
        label = "Last 30 days"
    else:
        try:
            start, end = month_bounds(period)
        except Exception:
            raise bad_request("period must be YYYY-MM or last30")
        end = min(end, t)
        # Compare month-to-date against the same number of days in the previous month.
        prev_start = add_months(start, -1)
        prev_end = min(add_months(end, -1), month_bounds(month_key(prev_start))[1])
        label = start.strftime("%B %Y") + (" (month to date)" if end == t else "")
    cur, prev = _period_totals(conn, biz, start, end), _period_totals(conn, biz, prev_start, prev_end)
    prev_full = prev
    ar = receivables(conn, biz)
    ap = payables(conn, biz)
    return {
        "period": period, "period_label": label, "period_start": iso(start), "period_end": iso(end), "as_of": iso(t),
        **cur,
        "previous_period_same_days": prev, "previous_period": {"start": iso(prev_start), "end": iso(prev_end), **prev_full},
        "revenue_change_pct": _pct(cur["revenue"], prev["revenue"]),
        "expenses_change_pct": _pct(cur["expenses"], prev["expenses"]),
        "cash_balance": cash_balance(conn, biz),
        "cash_inflow": cur["revenue"], "cash_outflow": cur["expenses"],
        "accounts_receivable": ar["total_open"], "overdue_receivables": ar["total_overdue"],
        "overdue_invoice_count": ar["overdue_count"],
        "accounts_payable": ap["total_open"], "payables_due_7_days": ap["due_7_days"],
        "definitions": METRIC_DEFINITIONS,
        "basis": "Recorded transactions (cash basis). Not certified accounting or tax advice.",
    }


def receivables(conn, biz: int) -> dict:
    t = today()
    rows = db.all_(conn, "SELECT * FROM invoices WHERE business_id=? AND kind='RECEIVABLE' AND status='OPEN' ORDER BY due_date", (biz,))
    s = settings(conn, biz)
    for r in rows:
        r["days_overdue"] = max(0, (t - parse_date(r["due_date"])).days)
        r["overdue"] = r["days_overdue"] > 0
        r["seriously_overdue"] = r["days_overdue"] > s["invoice_overdue_days"]
    overdue = [r for r in rows if r["overdue"]]
    return {"invoices": rows, "total_open": money(sum(r["amount"] for r in rows)),
            "total_overdue": money(sum(r["amount"] for r in overdue)), "overdue_count": len(overdue),
            "over_threshold_count": sum(1 for r in rows if r["seriously_overdue"]),
            "overdue_threshold_days": s["invoice_overdue_days"]}


def payables(conn, biz: int) -> dict:
    t = today()
    rows = db.all_(conn, "SELECT * FROM invoices WHERE business_id=? AND kind='PAYABLE' AND status='OPEN' ORDER BY due_date", (biz,))
    for r in rows:
        r["days_until_due"] = (parse_date(r["due_date"]) - t).days
    return {"invoices": rows, "total_open": money(sum(r["amount"] for r in rows)),
            "due_7_days": money(sum(r["amount"] for r in rows if r["days_until_due"] <= 7)),
            "due_7_days_count": sum(1 for r in rows if r["days_until_due"] <= 7)}


def list_transactions(conn, biz: int, direction=None, category=None, start=None, end=None, q=None,
                      limit: int = 50, offset: int = 0) -> dict:
    where, params = ["business_id=?"], [biz]
    for col, val in (("direction", direction), ("category", category)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    if start:
        where.append("txn_date>=?")
        params.append(start)
    if end:
        where.append("txn_date<=?")
        params.append(end)
    if q:
        where.append("(description LIKE ? OR counterparty LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    total = db.scalar(conn, f"SELECT COUNT(*) FROM transactions WHERE {w}", params)
    rows = db.all_(conn, f"SELECT * FROM transactions WHERE {w} ORDER BY txn_date DESC, id DESC LIMIT ? OFFSET ?",
                   params + [limit, offset])
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def record_transaction(conn, biz: int, data: dict, actor: str) -> dict:
    if data["direction"] not in ("INCOME", "EXPENSE"):
        raise bad_request("direction must be INCOME or EXPENSE")
    if not data.get("category"):
        data["category"] = suggest_category(data.get("description", ""), data["direction"])
    if data.get("document_id") and not db.get_owned(conn, "documents", data["document_id"], biz):
        raise bad_request("Unknown document")
    tid = db.insert(conn, "transactions", {"business_id": biz, **data})
    audit(conn, biz, actor_type_for(actor), actor, "transaction.create", "transaction", tid,
          {"amount": data["amount"], "direction": data["direction"], "category": data["category"]})
    return db.get_owned(conn, "transactions", tid, biz)


def update_transaction(conn, biz: int, tid: int, data: dict, actor: str) -> dict:
    if not db.get_owned(conn, "transactions", tid, biz):
        raise not_found("Transaction")
    db.update(conn, "transactions", tid, data, biz)
    audit(conn, biz, actor_type_for(actor), actor, "transaction.update", "transaction", tid, data)
    return db.get_owned(conn, "transactions", tid, biz)


def mark_invoice_paid(conn, biz: int, inv_id: int, actor: str) -> dict:
    inv = db.get_owned(conn, "invoices", inv_id, biz)
    if not inv:
        raise not_found("Invoice")
    if inv["status"] != "OPEN":
        raise bad_request("Invoice is not open")
    with db.transaction(conn):
        db.update(conn, "invoices", inv_id, {"status": "PAID", "paid_date": iso(today())}, biz)
        receivable = inv["kind"] == "RECEIVABLE"
        record_transaction(conn, biz, {
            "direction": "INCOME" if receivable else "EXPENSE",
            "category": "Catering" if receivable else "Supplier Payments",
            "amount": inv["amount"], "description": f"Payment for {inv['number']}", "txn_date": iso(today()),
            "counterparty": inv["counterparty"], "ref": inv["number"]}, actor)
        audit(conn, biz, actor_type_for(actor), actor, "invoice.paid", "invoice", inv_id, {"amount": inv["amount"]})
    return db.get_owned(conn, "invoices", inv_id, biz)


def category_breakdown(conn, biz: int, direction: str, start, end) -> list[dict]:
    return db.all_(conn, """SELECT category, ROUND(SUM(amount),2) AS total, COUNT(*) AS count FROM transactions
                            WHERE business_id=? AND direction=? AND txn_date>=? AND txn_date<=?
                            GROUP BY category ORDER BY total DESC""", (biz, direction, iso(start), iso(end)))


def monthly_series(conn, biz: int, months: int = 6) -> list[dict]:
    t = today()
    out = []
    for i in range(months - 1, -1, -1):
        m = month_key(add_months(t.replace(day=1), -i))
        s, e = month_bounds(m)
        tot = _period_totals(conn, biz, s, min(e, t))
        out.append({"month": m, **tot, "partial": e > t})
    return out


def cash_trend(conn, biz: int, days: int = 60) -> list[dict]:
    t = today()
    start = t - timedelta(days=days - 1)
    bal = cash_balance(conn, biz, start - timedelta(days=1))
    rows = db.all_(conn, """SELECT txn_date, direction, SUM(amount) AS a FROM transactions WHERE business_id=? AND status='PAID'
                            AND txn_date>=? AND txn_date<=? GROUP BY txn_date, direction""", (biz, iso(start), iso(t)))
    net = defaultdict(float)
    for r in rows:
        net[r["txn_date"]] += r["a"] if r["direction"] == "INCOME" else -r["a"]
    out = []
    for i in range(days):
        d = iso(start + timedelta(days=i))
        bal += net.get(d, 0)
        out.append({"date": d, "balance": money(bal)})
    return out


def _avg_daily(conn, biz: int, direction: str, category: str | None, days: int) -> float:
    t = today()
    sql = "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE business_id=? AND direction=? AND txn_date>? AND txn_date<=?"
    params = [biz, direction, iso(t - timedelta(days=days)), iso(t)]
    if category:
        sql += " AND category=?"
        params.append(category)
    return float(db.scalar(conn, sql, params) or 0) / days


def upcoming_obligations(conn, biz: int, days: int = 30) -> list[dict]:
    from . import payroll
    t = today()
    horizon = t + timedelta(days=days)
    s = settings(conn, biz)
    out = []
    for inv in payables(conn, biz)["invoices"]:
        due = parse_date(inv["due_date"])
        if due <= horizon:
            out.append({"date": iso(max(due, t)), "type": "Supplier bill", "description": f"{inv['number']} · {inv['counterparty']}",
                        "amount": inv["amount"], "basis": "recorded", "overdue": due < t})
    for r in db.all_(conn, "SELECT * FROM recurring_expenses WHERE business_id=? AND active=1", (biz,)):
        d = t.replace(day=r["day_of_month"]) if r["day_of_month"] >= t.day else add_months(t.replace(day=1), 1).replace(day=r["day_of_month"])
        while d <= horizon:
            # skip an occurrence that has already been paid (a matching transaction within a few days)
            paid = db.scalar(conn, """SELECT 1 FROM transactions WHERE business_id=? AND direction='EXPENSE' AND category=?
                                      AND txn_date BETWEEN ? AND ? LIMIT 1""",
                             (biz, r["category"], iso(d - timedelta(days=5)), iso(d + timedelta(days=5))))
            if not paid:
                out.append({"date": iso(d), "type": "Recurring", "description": r["name"], "amount": r["amount"],
                            "basis": "scheduled"})
            d = add_months(d, 1)
    pay_day = t.replace(day=min(s["payroll_day"], 28)) if s["payroll_day"] >= t.day else add_months(t.replace(day=1), 1).replace(day=min(s["payroll_day"], 28))
    while pay_day <= horizon:
        period = month_key(pay_day)
        run = db.one(conn, "SELECT * FROM payroll_runs WHERE business_id=? AND period=? AND status NOT IN ('CANCELLED')", (biz, period))
        if run and run["status"] == "PAID":
            pay_day = add_months(pay_day, 1)
            continue
        est = payroll.estimate(conn, biz, period)
        # Gross pay leaves the business either as net pay or as employee deductions remitted on their behalf.
        amount = run["total_gross"] if run else est["total_gross"]
        employer = run["total_employer_cost"] if run else est["total_employer_cost"]
        out.append({"date": iso(pay_day), "type": "Payroll", "description": f"Payroll {period} (gross pay)",
                    "amount": money(amount), "basis": "prepared run" if run else "estimate"})
        if employer:
            out.append({"date": iso(pay_day), "type": "Payroll", "description": f"Employer contributions {period}",
                        "amount": money(employer), "basis": "prepared run" if run else "estimate"})
        pay_day = add_months(pay_day, 1)
    # Purchase orders approved or awaiting approval are commitments not yet billed: due at ETA + payment terms.
    import re as _re
    for po in db.all_(conn, """SELECT po.*, s.name AS supplier, s.payment_terms FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
                               WHERE po.business_id=? AND po.status IN ('AWAITING_APPROVAL','APPROVED','SUBMITTED')""", (biz,)):
        m = _re.search(r"(\d+)", po["payment_terms"] or "")
        due = (parse_date(po["expected_date"]) or t) + timedelta(days=int(m.group(1)) if m else 30)
        if due <= horizon:
            out.append({"date": iso(max(due, t)), "type": "Purchase order",
                        "description": f"{po['po_number']} · {po['supplier']} ({po['status'].replace('_', ' ').lower()})",
                        "amount": po["total"], "basis": "open order, not yet billed"})
    out.sort(key=lambda x: x["date"])
    return out


def forecast(conn, biz: int, days: int = 30) -> dict:
    """Two transparent scenarios:
    - committed: today's cash + receivables due in the window (not already overdue) - recorded/scheduled obligations
    - expected:  committed + estimated sales inflow - estimated routine supplier purchases (28-day averages)
    """
    t = today()
    days = max(7, min(120, days))
    start_cash = cash_balance(conn, biz)
    obligations = upcoming_obligations(conn, biz, days)
    ar = receivables(conn, biz)
    inflows = [{"date": inv["due_date"], "type": "Customer invoice", "description": f"{inv['number']} · {inv['counterparty']}",
                "amount": inv["amount"], "basis": "recorded"}
               for inv in ar["invoices"] if not inv["overdue"] and parse_date(inv["due_date"]) <= t + timedelta(days=days)]
    avg_sales = _avg_daily(conn, biz, "INCOME", "Sales", 28)
    avg_supplier = _avg_daily(conn, biz, "EXPENSE", "Supplier Payments", 28)
    by_day_committed = defaultdict(float)
    for o in obligations:
        by_day_committed[o["date"]] -= o["amount"]
    for i in inflows:
        by_day_committed[i["date"]] += i["amount"]
    # Items due today (or already overdue, which are dated today) hit the balance immediately.
    day0 = start_cash + by_day_committed.get(iso(t), 0)
    series, committed, expected = [{"date": iso(t), "committed": money(day0), "expected": money(day0)}], day0, day0
    low_c, low_e = (day0, iso(t)), (day0, iso(t))
    for k in range(1, days + 1):
        d = iso(t + timedelta(days=k))
        committed += by_day_committed.get(d, 0)
        expected += by_day_committed.get(d, 0) + avg_sales - avg_supplier
        series.append({"date": d, "committed": money(committed), "expected": money(expected)})
        if committed < low_c[0]:
            low_c = (committed, d)
        if expected < low_e[0]:
            low_e = (expected, d)
    total_obl = money(sum(o["amount"] for o in obligations))
    total_in = money(sum(i["amount"] for i in inflows))
    return {
        "as_of": iso(t), "horizon_days": days, "starting_cash": start_cash,
        "obligations": obligations, "total_obligations": total_obl,
        "expected_receipts": inflows, "total_expected_receipts": total_in,
        "overdue_receivables_excluded": ar["total_overdue"],
        "estimated_daily_sales_inflow": money(avg_sales), "estimated_daily_supplier_purchases": money(avg_supplier),
        "projected_committed_balance": money(committed), "projected_expected_balance": money(expected),
        "lowest_committed_balance": money(low_c[0]), "lowest_committed_date": low_c[1],
        "lowest_expected_balance": money(low_e[0]), "lowest_expected_date": low_e[1],
        "series": series,
        "assumptions": [
            "Committed scenario: only recorded invoices, scheduled recurring expenses and payroll (estimated from employee records if not yet prepared).",
            "Overdue customer invoices are excluded from inflows until collected.",
            f"Expected scenario adds average daily sales ({money(avg_sales)}) and routine supplier purchases ({money(avg_supplier)}) from the last 28 days.",
            "Figures are projections, not guarantees.",
        ],
    }


def spending_anomalies(conn, biz: int, threshold_pct: float = 15.0) -> list[dict]:
    """Compare each expense category over the last 30 days with the average of the three prior 30-day windows."""
    t = today()
    start = t - timedelta(days=29)
    cur = {r["category"]: r["total"] for r in category_breakdown(conn, biz, "EXPENSE", start, t)}
    hist = defaultdict(list)
    for i in range(1, 4):
        ps, pe = start - timedelta(days=30 * i), start - timedelta(days=30 * (i - 1) + 1)
        for r in category_breakdown(conn, biz, "EXPENSE", ps, pe):
            hist[r["category"]].append(r["total"])
    out = []
    for cat, amt in cur.items():
        vals = hist.get(cat, [])
        if not vals:
            continue
        avg = sum(vals) / 3
        if avg > 0 and (amt - avg) / avg * 100 >= threshold_pct and amt - avg >= 50:
            out.append({"category": cat, "this_period": money(amt), "prior_30_day_windows_avg": money(avg),
                        "increase_pct": round((amt - avg) / avg * 100, 1), "increase_amount": money(amt - avg)})
    out.sort(key=lambda x: -x["increase_pct"])
    return out


def budget_status(conn, biz: int) -> list[dict]:
    s = settings(conn, biz)
    budgets = s["budgets"] if isinstance(s["budgets"], dict) else {}
    t = today()
    spent = {r["category"]: r["total"] for r in category_breakdown(conn, biz, "EXPENSE", t.replace(day=1), t)}
    return [{"category": c, "budget": money(b), "spent": money(spent.get(c, 0)),
             "used_pct": round(spent.get(c, 0) / b * 100, 1) if b else None, "over": spent.get(c, 0) > b}
            for c, b in budgets.items()]
