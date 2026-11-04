"""Structured answer cards.

Built deterministically from the tool results an assistant run produced, so every figure on a card
comes straight from the business services (the same data the verifier checks the prose against).
The UI renders these as visual cards; the model's prose becomes a short summary on top.
"""
from __future__ import annotations


def _latest(tool_outputs: list, *names: str):
    for o in reversed(tool_outputs):
        if o["tool"] in names and isinstance(o.get("result"), dict):
            return o["result"]
    return None


def build_cards(tool_outputs: list, currency: str) -> list[dict]:
    cards: list[dict] = []
    fs = _latest(tool_outputs, "get_financial_summary")
    ar = _latest(tool_outputs, "get_accounts_receivable")
    pe = _latest(tool_outputs, "estimate_payroll")
    fc = _latest(tool_outputs, "forecast_cash_balance")
    snap = _latest(tool_outputs, "get_business_health_snapshot")

    # ---- business pulse
    tiles = []
    if fs:
        tiles.append({"label": "Cash balance", "value": fs["cash_balance"], "sub": "recorded", "tone": "ink"})
        tiles.append({"label": f"Revenue, {fs.get('period_label', 'period').lower()}", "value": fs["revenue"],
                      "delta": fs.get("revenue_change_pct"), "tone": "ink"})
    elif snap:
        tiles.append({"label": "Cash balance", "value": snap["cash_balance"], "sub": "recorded", "tone": "ink"})
        tiles.append({"label": "Revenue, last 30 days", "value": snap["month_to_date"]["revenue"],
                      "delta": snap["month_to_date"].get("revenue_change_pct_vs_same_days_last_month"), "tone": "ink"})
    if pe:
        tiles.append({"label": f"Payroll due {pe.get('pay_date', '')}", "value": pe["total_cost_to_business"], "sub": "estimate",
                      "tone": "honey"})
    elif snap:
        tiles.append({"label": f"Payroll due {snap['payroll']['date']}", "value": snap["payroll"]["estimated_total_cost"],
                      "sub": "estimate", "tone": "honey"})
    if ar and ar.get("overdue_count"):
        tiles.append({"label": f"{ar['overdue_count']} overdue invoices", "value": ar["total_overdue"], "sub": "to collect",
                      "tone": "red"})
    elif snap and snap["receivables"]["overdue_count"]:
        tiles.append({"label": f"{snap['receivables']['overdue_count']} overdue invoices", "value": snap["receivables"]["overdue_total"],
                      "sub": "to collect", "tone": "red"})
    if fc:
        tiles.append({"label": "Cash in 30 days", "value": fc["projected_committed_balance"], "sub": "committed scenario",
                      "tone": "red" if fc["projected_committed_balance"] < 0 else "ink"})
    if tiles:
        cards.append({"type": "pulse", "title": "Business pulse", "tiles": tiles[:5]})

    # ---- stock & restock
    plan = _latest(tool_outputs, "get_restock_plan")
    low = _latest(tool_outputs, "get_low_stock_products")
    if plan and plan.get("items"):
        rows = [{"product": i["product"], "unit": i.get("unit"), "stock_pct": i["stock_pct"], "level": i.get("level"),
                 "current": i.get("current_stock"), "max": i.get("max_stock"), "cover": i.get("days_of_cover"),
                 "supplier": i.get("supplier"), "quantity": i.get("quantity"), "cost": i.get("line_total"), "eta": i.get("eta"),
                 "on_order": i.get("already_on_order"), "why": i.get("rationale")} for i in plan["items"]]
        drafted = _latest(tool_outputs, "draft_restock_purchase_orders")
        if drafted:
            for row in rows:
                for c in drafted.get("created", []):
                    if any(it.endswith(" " + row["product"]) for it in c.get("items", [])):
                        row["drafted"] = c["po_number"]
        cards.append({"type": "stock", "title": "Restock plan", "subtitle": "Refill to capacity, rounded to supplier minimums",
                      "rows": rows, "total": None if drafted else plan.get("estimated_total")})
    elif low and low.get("products"):
        rows = [{"product": p["name"], "unit": p.get("unit"), "stock_pct": p["stock_pct"], "current": p.get("current_stock"),
                 "max": p.get("max_stock"), "cover": p.get("days_of_cover"), "supplier": p.get("supplier_name")}
                for p in low["products"]]
        cards.append({"type": "stock", "title": "Below the stock threshold", "rows": rows})

    # ---- affordability runway
    af = _latest(tool_outputs, "evaluate_affordability")
    if af:
        segs = [{"label": "Payroll", "value": af["payroll_due"]["value"]},
                {"label": "Supplier bills", "value": af["supplier_bills_due"]["value"]},
                {"label": "Open orders", "value": af.get("open_purchase_orders_due", {}).get("value", 0)},
                {"label": "Recurring costs", "value": af["recurring_expenses_due"]["value"]},
                {"label": "New restock", "value": af["recommended_restock_cost"]["value"]}]
        cards.append({"type": "runway", "title": f"Can you afford the next {af['horizon_days']} days?",
                      "cash": af["current_cash_balance"]["value"], "receipts": af["expected_customer_receipts_not_overdue"]["value"],
                      "segments": [s for s in segs if s["value"]], "outflows": af["total_committed_outflows"],
                      "remaining": af["projected_balance_after_commitments"]["value"], "affordable": af["affordable"],
                      "upside": af["overdue_receivables_upside"]["value"]})

    # ---- payroll
    pp = _latest(tool_outputs, "prepare_payroll")
    if pp or pe:
        src = pp or pe
        issues = [a for a in src.get("anomalies", []) if a.get("severity") == "HIGH"]
        cards.append({"type": "payroll", "title": f"Payroll {src.get('period', '')}", "pay_date": (pe or {}).get("pay_date"),
                      "gross": src.get("total_gross"), "net": src.get("total_net"), "employer": src.get("total_employer_cost"),
                      "status": pp.get("status") if pp else "ESTIMATE",
                      "issues": [{"who": a.get("employee"), "issue": a.get("issue")} for a in issues]})

    # ---- contracts
    ec = _latest(tool_outputs, "get_expiring_contracts", "get_contracts")
    cd = _latest(tool_outputs, "get_contract_details")
    items = list((ec or {}).get("contracts", []))
    if cd and not any(i.get("id") == cd.get("id") for i in items):
        items.append(cd)
    if items:
        cards.append({"type": "contracts", "title": "Contract deadlines", "items": [
            {k: c.get(k) for k in ("id", "title", "counterparty", "end_date", "days_to_expiry", "notice_deadline",
                                   "days_to_notice_deadline", "auto_renew", "value", "value_period", "payment_terms")}
            for c in items]})

    # ---- priorities
    recs = _latest(tool_outputs, "get_recommendations")
    if recs and recs.get("recommendations"):
        cards.append({"type": "priorities", "title": "Top priorities", "items": [
            {"title": r["title"], "action": r.get("suggested_action"), "priority": r["priority"], "agent": r.get("agent")}
            for r in recs["recommendations"][:4]]})
    for c in cards:
        c["currency"] = currency
    return cards
