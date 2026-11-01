"""Business Intelligence detectors. Every recommendation is produced by a deterministic detector
with its supporting data attached; the AI layer may explain or prioritise but never invents figures."""
from __future__ import annotations

import hashlib
import json

from .. import db
from . import contracts, finance, inventory, payroll, procurement
from .common import add_months, money, month_bounds, month_key, now_iso, today

CATEGORIES = ["Revenue", "Cost optimisation", "Inventory", "Finance", "Workforce", "Procurement", "Risk & compliance"]


def _rec(fp, title, category, description, data, impact, priority, action, module, agent, benefit=None, confidence="HIGH"):
    return {"fingerprint": hashlib.sha1(fp.encode()).hexdigest()[:16], "title": title, "category": category,
            "description": description, "supporting_data": data, "impact": impact, "priority": priority,
            "suggested_action": action, "estimated_benefit": benefit, "confidence": confidence, "module": module,
            "agent": agent}


def detect(conn, biz: int) -> list[dict]:
    cur = (db.one(conn, "SELECT currency FROM businesses WHERE id=?", (biz,)) or {}).get("currency", "")
    recs = []
    # 1. Stockout risk
    plan = procurement.restock_plan(conn, biz)
    for line in plan["items"]:
        if line.get("already_on_order"):
            continue
        pr = ("HIGH" if line["level"] in ("URGENT", "CRITICAL", "OUT") or line.get("stockout_risk") == "HIGH"
              or (line.get("days_of_cover") is not None and line["days_of_cover"] <= 5) else "MEDIUM")
        cover = f"{line['days_of_cover']} days" if line.get("days_of_cover") is not None else "unknown time"
        recs.append(_rec(
            f"stockout:{line['product_id']}:{line['level']}", f"Restock {line['product']}", "Inventory",
            f"{line['product']} is at {line['stock_pct']}% of capacity ({line['current_stock']:g}/{line['max_stock']:g} {line['unit']}), "
            f"about {cover} of cover at the current sales rate.",
            {k: line.get(k) for k in ("stock_pct", "current_stock", "max_stock", "days_of_cover", "stockout_risk",
                                      "supplier", "quantity", "unit_price", "line_total", "eta")},
            "Avoids lost sales from a stockout.", pr,
            f"Order {line.get('quantity', 0):g} {line['unit']} from {line.get('supplier', 'a supplier')} "
            f"(~{cur} {line.get('line_total', 0):,.2f}).", "Inventory", "Inventory Agent"))
    # 2. Overdue receivables
    ar = finance.receivables(conn, biz)
    over = [i for i in ar["invoices"] if i["seriously_overdue"]]
    if over:
        amt = money(sum(i["amount"] for i in over))
        recs.append(_rec(f"overdue:{','.join(str(i['id']) for i in over)}",
                         f"Collect {len(over)} overdue customer invoice{'s' if len(over) > 1 else ''}", "Finance",
                         f"{len(over)} invoice(s) totalling {cur} {amt:,.2f} are more than {ar['overdue_threshold_days']} days overdue.",
                         {"invoices": [{"number": i["number"], "customer": i["counterparty"], "amount": i["amount"],
                                        "days_overdue": i["days_overdue"]} for i in over], "total": amt},
                         "Improves short-term cash position.", "HIGH", "Send payment reminders and follow up by phone.",
                         "Finance", "Finance Agent", benefit=amt))
    # 3. Bills due soon
    ap = finance.payables(conn, biz)
    if ap["due_7_days_count"]:
        recs.append(_rec(f"payables7:{ap['due_7_days']}", f"{ap['due_7_days_count']} supplier bill(s) due within 7 days",
                         "Finance", f"Supplier bills totalling {cur} {ap['due_7_days']:,.2f} fall due within a week.",
                         {"bills": [{"number": i["number"], "supplier": i["counterparty"], "amount": i["amount"],
                                     "days_until_due": i["days_until_due"]} for i in ap["invoices"] if i["days_until_due"] <= 7]},
                         "Avoids late fees and protects supplier relationships.", "MEDIUM",
                         "Schedule payment before the due dates.", "Finance", "Finance Agent"))
    # 4. Unusual spending
    for a in finance.spending_anomalies(conn, biz):
        recs.append(_rec(f"spend:{a['category']}:{month_key(today())}", f"{a['category']} spending up {a['increase_pct']:.0f}%",
                         "Cost optimisation",
                         f"{a['category']} over the last 30 days is {cur} {a['this_period']:,.2f} vs an average of "
                         f"{cur} {a['prior_30_day_windows_avg']:,.2f} over the previous three 30-day periods.", a,
                         "Unexplained cost increases erode margin.", "MEDIUM" if a["increase_pct"] < 40 else "HIGH",
                         f"Review recent {a['category'].lower()} bills to identify the source of the increase.",
                         "Finance", "Finance Agent", benefit=a["increase_amount"]))
    # 5. Supplier price increases
    for c in procurement.price_changes(conn, biz):
        if c["change_pct"] and c["change_pct"] >= 5:
            alt = db.one(conn, """SELECT s.name, sp.unit_price FROM supplier_products sp JOIN suppliers s ON s.id=sp.supplier_id
                                  JOIN products p ON p.id=sp.product_id WHERE sp.business_id=? AND p.name=? AND sp.supplier_id<>?
                                  ORDER BY sp.unit_price LIMIT 1""", (biz, c["product"], c["supplier_id"]))
            recs.append(_rec(f"price:{c['supplier']}:{c['product']}:{c['unit_price']}",
                             f"{c['supplier']} raised {c['product']} price {c['change_pct']:.0f}%", "Procurement",
                             f"Unit price moved from {c['previous_unit_price']:.2f} to {c['unit_price']:.2f}."
                             + (f" {alt['name']} quotes {alt['unit_price']:.2f}." if alt else ""),
                             {**c, "alternative": alt}, "Higher input costs reduce margin.", "MEDIUM",
                             "Review supplier pricing or renegotiate before the next order.", "Procurement", "Procurement Agent"))
    # 6. Payroll as % of revenue
    t = today()
    prev = month_key(add_months(t.replace(day=1), -1))
    s, e = month_bounds(prev)
    rev = finance._period_totals(conn, biz, s, e)["revenue"]
    est = payroll.estimate(conn, biz, month_key(t))
    cost = est["total_cost_to_business"]
    if rev and cost / rev > 0.30:
        ratio = round(cost / rev * 100, 1)
        recs.append(_rec(f"payrollratio:{month_key(t)}:{int(ratio)}", f"Payroll is {ratio}% of revenue", "Workforce",
                         f"Estimated payroll cost {cur} {cost:,.2f} vs last month's revenue {cur} {rev:,.2f}.",
                         {"payroll_cost": cost, "last_month_revenue": rev, "ratio_pct": ratio}, "Labour cost is the largest controllable expense.",
                         "LOW" if ratio < 40 else "MEDIUM",
                         "Review rosters against hourly sales patterns before making workforce changes.", "Payroll", "HR & Payroll Agent",
                         confidence="MEDIUM"))
    high_anoms = [a for a in est["anomalies"] if a["severity"] == "HIGH"]
    if high_anoms:
        recs.append(_rec(f"payrollanom:{month_key(t)}:{len(high_anoms)}", f"Fix {len(high_anoms)} payroll data issue(s) before payday",
                         "Workforce", "; ".join(f"{a['employee']}: {a['issue']}" for a in high_anoms),
                         {"issues": high_anoms}, "Prevents incorrect or failed salary payments.", "HIGH",
                         "Update employee records/attendance, then prepare payroll.", "Payroll", "HR & Payroll Agent"))
    # 7. Contract notice deadlines / expiries
    for c in contracts.list_contracts(conn, biz):
        if c["days_to_notice_deadline"] is not None and 0 <= c["days_to_notice_deadline"] <= 30:
            recs.append(_rec(f"notice:{c['id']}:{c['notice_deadline']}", f"Decide on {c['title']} renewal",
                             "Risk & compliance",
                             f"Renews automatically on {c['end_date']} unless notice is given by {c['notice_deadline']} "
                             f"({c['days_to_notice_deadline']} days).",
                             {"contract_id": c["id"], "end_date": c["end_date"], "notice_deadline": c["notice_deadline"],
                              "notice_days": c["notice_days"], "counterparty": c["counterparty"]},
                             "Avoids being locked into another term unintentionally.", "HIGH",
                             "Review terms and pricing; send notice if you do not want to renew.", "Contracts", "Contract Agent"))
        elif c["days_to_expiry"] is not None and 0 <= c["days_to_expiry"] <= 60 and not c["auto_renew"]:
            recs.append(_rec(f"expiry:{c['id']}:{c['end_date']}", f"{c['title']} expires in {c['days_to_expiry']} days",
                             "Risk & compliance", f"Contract with {c['counterparty']} ends on {c['end_date']}.",
                             {"contract_id": c["id"], "end_date": c["end_date"]}, "Avoids service gaps.",
                             "MEDIUM" if c["days_to_expiry"] > 14 else "HIGH", "Start renewal discussions.", "Contracts",
                             "Contract Agent"))
    # 8. Cash-flow risk
    fc = finance.forecast(conn, biz, 30)
    if fc["lowest_committed_balance"] < 0:
        recs.append(_rec(f"cashrisk:{fc['lowest_committed_date']}", "Projected cash shortfall", "Finance",
                         f"Committed obligations could take cash to {cur} {fc['lowest_committed_balance']:,.2f} on {fc['lowest_committed_date']} "
                         "before counting new sales.", {k: fc[k] for k in ("starting_cash", "total_obligations",
                                                                             "lowest_committed_balance", "lowest_committed_date")},
                         "Risk of missed payments.", "HIGH", "Collect overdue invoices and phase non-urgent purchases.",
                         "Finance", "Finance Agent"))
    # 9. Revenue trend
    sm = finance.summary(conn, biz)
    if sm["revenue_change_pct"] is not None and abs(sm["revenue_change_pct"]) >= 8:
        up = sm["revenue_change_pct"] > 0
        recs.append(_rec(f"revtrend:{month_key(t)}:{'up' if up else 'down'}",
                         f"Revenue {'up' if up else 'down'} {abs(sm['revenue_change_pct']):.0f}% over the last 30 days", "Revenue",
                         f"Revenue for the last 30 days was {cur} {sm['revenue']:,.2f} vs {cur} "
                         f"{sm['previous_period_same_days']['revenue']:,.2f} for the 30 days before.",
                         {"revenue": sm["revenue"], "previous": sm["previous_period_same_days"]["revenue"],
                          "change_pct": sm["revenue_change_pct"]},
                         "Signals demand change.", "LOW" if up else "MEDIUM",
                         "Ensure best-sellers stay in stock." if up else "Review promotions and opening hours.",
                         "Finance", "Business Intelligence Agent", confidence="MEDIUM"))
    # 10. Slow movers / overstock
    for p in inventory.list_products(conn, biz):
        if p["level"] == "HEALTHY" and p.get("days_of_cover") and p["days_of_cover"] > 60 and p["stock_pct"] >= 45:
            tied = money(p["current_stock"] * p["unit_cost"])
            recs.append(_rec(f"slow:{p['id']}:{int(p['days_of_cover'] // 10)}", f"{p['name']} is moving slowly", "Revenue",
                             f"{p['current_stock']:g} {p['unit']} on hand cover ~{p['days_of_cover']:.0f} days of sales; "
                             f"{cur} {tied:,.2f} of cash is tied up.",
                             {"days_of_cover": p["days_of_cover"], "stock_value": tied, "avg_daily_sales": p["avg_daily_sales"]},
                             "Frees working capital.", "LOW", "Run a promotion or reduce the next order size.", "Inventory",
                             "Business Intelligence Agent", confidence="MEDIUM"))
    return recs


PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def refresh(conn, biz: int) -> dict:
    """Upsert detected recommendations; auto-complete pending ones whose condition disappeared."""
    detected = detect(conn, biz)
    fps = set()
    new = 0
    for r in detected:
        fps.add(r["fingerprint"])
        ex = db.one(conn, "SELECT id, status FROM recommendations WHERE business_id=? AND fingerprint=?", (biz, r["fingerprint"]))
        payload = {k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in r.items()}
        if ex:
            upd = {k: v for k, v in payload.items() if k != "fingerprint"}
            upd["updated_at"] = now_iso()
            if ex["status"] == "COMPLETED":
                upd["status"] = "PENDING"
            db.update(conn, "recommendations", ex["id"], upd, biz)
        else:
            db.insert(conn, "recommendations", {"business_id": biz, **payload})
            new += 1
    stale = db.all_(conn, "SELECT id, fingerprint FROM recommendations WHERE business_id=? AND status IN ('PENDING','ACCEPTED')", (biz,))
    resolved = 0
    for s in stale:
        if s["fingerprint"] not in fps:
            db.update(conn, "recommendations", s["id"], {"status": "COMPLETED", "updated_at": now_iso()}, biz)
            resolved += 1
    return {"detected": len(detected), "new": new, "auto_resolved": resolved}


def list_(conn, biz: int, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM recommendations WHERE business_id=?"
    params: list = [biz]
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = db.all_(conn, sql, params)
    rows.sort(key=lambda r: (r["status"] not in ("PENDING", "ACCEPTED"), PRIORITY_ORDER[r["priority"]], -r["id"]))
    return rows


def top(conn, biz: int, n: int = 5) -> list[dict]:
    return [r for r in list_(conn, biz) if r["status"] in ("PENDING", "ACCEPTED")][:n]
