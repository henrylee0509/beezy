"""Tool contracts for the specialised agents.

Agents never touch the database directly: they call these predefined tools. Each tool declares
the permission it needs; the orchestrator only exposes tools the signed-in user's role allows,
and every argument is validated before execution. "Action" tools can only create drafts and
approval requests — nothing that moves money executes without a human approval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from .. import db
from ..services import (contracts, dashboard, finance, insights, inventory, payroll, procurement)
from ..services.common import iso, money, month_key, parse_date, today


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    agent: str
    description: str
    parameters: dict
    handler: Callable
    perm: str
    action: bool = False
    label: str = ""

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": self.parameters}}


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


PRODUCT_REF = {"type": "string", "description": "Product name, SKU or numeric id"}


def validate_args(schema: dict, args: Any) -> dict:
    """Minimal JSON-schema validation for tool arguments (types, required, enums, bounds)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("Arguments must be a JSON object")
    props = schema.get("properties", {})
    for r in schema.get("required", []):
        if r not in args or args[r] in (None, ""):
            raise ToolError(f"Missing required argument '{r}'")
    clean = {}
    for k, v in args.items():
        if k not in props:
            continue  # ignore unknown args rather than failing the whole run
        p = props[k]
        t = p.get("type")
        if v is None:
            continue
        if t == "integer":
            if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, (float, str)) and str(v).strip().lstrip("-").replace(".0", "").isdigit())):
                raise ToolError(f"'{k}' must be an integer")
            v = int(float(v))
        elif t == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ToolError(f"'{k}' must be a number")
        elif t == "string":
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                v = str(v)
            if not isinstance(v, str):
                raise ToolError(f"'{k}' must be a string")
            v = v.strip()[:500]
        elif t == "boolean":
            if not isinstance(v, bool):
                raise ToolError(f"'{k}' must be a boolean")
        elif t == "array":
            if not isinstance(v, list):
                raise ToolError(f"'{k}' must be an array")
            if p.get("items", {}).get("type") == "integer":
                try:
                    v = [int(x) for x in v]
                except (TypeError, ValueError):
                    raise ToolError(f"'{k}' must be an array of integers")
            if len(v) > 50:
                raise ToolError(f"'{k}' has too many items")
        if "enum" in p and v not in p["enum"]:
            raise ToolError(f"'{k}' must be one of {p['enum']}")
        if "minimum" in p and isinstance(v, (int, float)) and v < p["minimum"]:
            raise ToolError(f"'{k}' must be ≥ {p['minimum']}")
        if "maximum" in p and isinstance(v, (int, float)) and v > p["maximum"]:
            raise ToolError(f"'{k}' must be ≤ {p['maximum']}")
        clean[k] = v
    return clean


def _actor(ctx, agent):
    return f"agent:{agent} (for {ctx.name})"


def _product_brief(p):
    return {k: p.get(k) for k in ("id", "name", "sku", "category", "unit", "current_stock", "max_stock", "stock_pct", "status_label",
                                  "avg_daily_sales", "days_of_cover", "depletion_date", "stockout_risk", "supplier_name", "unit_cost")}


# ---------------- Inventory Agent ----------------
def t_get_all_inventory(conn, ctx, a):
    s = inventory.settings(conn, ctx.business_id)
    return {"threshold_rule": f"Alert when stock < {s['low_stock_pct']:g}% of max capacity (urgent < {s['urgent_stock_pct']:g}%, critical < {s['critical_stock_pct']:g}%)",
            "products": [_product_brief(p) for p in inventory.list_products(conn, ctx.business_id)],
            "inventory_value_at_cost": inventory.inventory_value(conn, ctx.business_id)}


def t_get_low_stock(conn, ctx, a):
    low = inventory.low_stock_products(conn, ctx.business_id)
    return {"count": len(low), "products": [_product_brief(p) for p in low],
            "formula": "Stock Percentage = Current Stock / Maximum Stock Capacity × 100"}


def t_get_inventory(conn, ctx, a):
    return _product_brief(inventory.find_product(conn, ctx.business_id, a["product"]))


def t_stock_history(conn, ctx, a):
    p = inventory.find_product(conn, ctx.business_id, a["product"])
    return {"product": p["name"], "daily_usage_last_14_days": inventory.daily_sales_series(conn, ctx.business_id, p["id"], 14),
            "recent_movements": [{k: m[k] for k in ("kind", "quantity", "note", "occurred_on")}
                                 for m in inventory.movements(conn, ctx.business_id, p["id"], 10)]}


def t_forecast(conn, ctx, a):
    p = inventory.find_product(conn, ctx.business_id, a["product"])
    return inventory.forecast(conn, ctx.business_id, p["id"])


def t_supplier_for_product(conn, ctx, a):
    p = inventory.find_product(conn, ctx.business_id, a["product"])
    return {"product": p["name"], "preferred_supplier": p.get("supplier_name"),
            "all_quotes": compare_slim(procurement.compare_quotes(conn, ctx.business_id, p["id"]))["quotes"]}


# ---------------- Procurement Agent ----------------
def compare_slim(c):
    return {**{k: c[k] for k in ("product", "days_of_cover", "quantity_needed", "rationale") if k in c},
            "quotes": [{k: q[k] for k in ("supplier", "unit_price", "min_order_qty", "lead_time_days", "eta", "order_quantity",
                                          "total_cost", "reliability", "arrives_before_stockout", "recommended", "price_change_pct")}
                       for q in c["quotes"]]}


def t_get_suppliers(conn, ctx, a):
    return {"suppliers": [{"id": s["id"], "name": s["name"], "payment_terms": s["payment_terms"], "reliability": s["reliability"],
                           "preferred": bool(s["is_preferred"]), "on_time_rate_pct": s["performance"]["on_time_rate_pct"],
                           "products": [{"product": p["product"], "unit_price": p["unit_price"], "moq": p["min_order_qty"],
                                         "lead_time_days": p["lead_time_days"]} for p in s["products"]]}
                          for s in procurement.list_suppliers(conn, ctx.business_id)]}


def t_compare_quotes(conn, ctx, a):
    p = inventory.find_product(conn, ctx.business_id, a["product"])
    return compare_slim(procurement.compare_quotes(conn, ctx.business_id, p["id"], a.get("quantity")))


def t_restock_plan(conn, ctx, a):
    return procurement.restock_plan(conn, ctx.business_id)


def t_draft_restock_pos(conn, ctx, a):
    ids = None
    if a.get("products"):
        ids = [inventory.find_product(conn, ctx.business_id, ref)["id"] for ref in a["products"]]
    return procurement.draft_restock_orders(conn, ctx.business_id, _actor(ctx, "Procurement Agent"), ids, origin="agent")


def t_create_po(conn, ctx, a):
    sup = procurement.find_supplier(conn, ctx.business_id, a["supplier"])
    p = inventory.find_product(conn, ctx.business_id, a["product"])
    po = procurement.create_po(conn, ctx.business_id, sup["id"], [{"product_id": p["id"], "quantity": a["quantity"]}],
                               _actor(ctx, "Procurement Agent"), origin="agent", notes=a.get("note"))
    return {"po_id": po["id"], "po_number": po["po_number"], "supplier": po["supplier"], "total": po["total"],
            "status": po["status"], "approval_id": (po.get("approval") or {}).get("id"),
            "items": [f"{i['quantity']:g} {i['unit']} {i['product']}" for i in po["items"]]}


def t_get_pos(conn, ctx, a):
    pos = procurement.list_pos(conn, ctx.business_id, a.get("status"))
    return {"purchase_orders": [{"po_number": p["po_number"], "supplier": p["supplier"], "status": p["status"], "total": p["total"],
                                 "expected_date": p["expected_date"],
                                 "items": [f"{i['quantity']:g} {i['product']}" for i in p["items"]]} for p in pos[:15]]}


def t_supplier_performance(conn, ctx, a):
    s = procurement.find_supplier(conn, ctx.business_id, a["supplier"])
    return {"supplier": s["name"], "reliability_rating": s["reliability"], **s["performance"],
            "price_changes": [c for c in procurement.price_changes(conn, ctx.business_id) if c["supplier"] == s["name"]]}


# ---------------- Finance Agent ----------------
def t_fin_summary(conn, ctx, a):
    s = finance.summary(conn, ctx.business_id, a.get("period"))
    s.pop("definitions", None)
    s["definitions"] = {k: v for k, v in finance.METRIC_DEFINITIONS.items() if k in ("net_operating_profit", "cash_balance")}
    return s


def t_transactions(conn, ctx, a):
    t = today()
    r = finance.list_transactions(conn, ctx.business_id, a.get("direction"), a.get("category"),
                                  a.get("start_date") or iso(t - timedelta(days=30)), a.get("end_date") or iso(t),
                                  limit=min(int(a.get("limit") or 20), 40))
    by_cat = {}
    for x in finance.list_transactions(conn, ctx.business_id, a.get("direction"), a.get("category"),
                                       a.get("start_date") or iso(t - timedelta(days=30)), a.get("end_date") or iso(t), limit=2000)["items"]:
        key = f"{x['direction']}:{x['category']}"
        by_cat[key] = money(by_cat.get(key, 0) + x["amount"])
    return {"total_matching": r["total"], "totals_by_category": by_cat,
            "latest": [{k: x[k] for k in ("txn_date", "direction", "category", "amount", "description", "counterparty", "status")}
                       for x in r["items"]]}


def t_receivables(conn, ctx, a):
    r = finance.receivables(conn, ctx.business_id)
    r["invoices"] = [{k: i[k] for k in ("number", "counterparty", "amount", "due_date", "days_overdue")} for i in r["invoices"]]
    return r


def t_payables(conn, ctx, a):
    r = finance.payables(conn, ctx.business_id)
    r["invoices"] = [{k: i[k] for k in ("number", "counterparty", "amount", "due_date", "days_until_due")} for i in r["invoices"]]
    return r


def t_forecast_cash(conn, ctx, a):
    f = finance.forecast(conn, ctx.business_id, int(a.get("days") or 30))
    f.pop("series", None)
    return f


def t_obligations(conn, ctx, a):
    ob = finance.upcoming_obligations(conn, ctx.business_id, int(a.get("days") or 30))
    return {"obligations": ob, "total": money(sum(o["amount"] for o in ob))}


def t_anomalies(conn, ctx, a):
    return {"categories_with_unusual_increase": finance.spending_anomalies(conn, ctx.business_id),
            "budgets": finance.budget_status(conn, ctx.business_id)}


def t_record_txn(conn, ctx, a):
    tx = finance.record_transaction(conn, ctx.business_id, {
        "direction": a["direction"], "category": a.get("category") or None, "amount": a["amount"],
        "description": a["description"], "txn_date": a.get("date") or iso(today()), "counterparty": a.get("counterparty")},
        _actor(ctx, "Finance Agent"))
    return {"recorded": True, "transaction_id": tx["id"], "category": tx["category"], "amount": tx["amount"]}


# ---------------- HR & Payroll Agent ----------------
def t_employees(conn, ctx, a):
    rows = payroll.list_employees(conn, ctx.business_id, ctx.can("salary.read"))
    return {"salary_visible": ctx.can("salary.read"),
            "employees": [{k: e[k] for k in ("emp_code", "name", "job_title", "employment_type", "status", "base_salary", "hourly_rate",
                                              "allowance", "payment_details_on_file")} for e in rows]}


def t_estimate_payroll(conn, ctx, a):
    period = a.get("period") or month_key(payroll.next_payroll_date(conn, ctx.business_id))
    e = payroll.estimate(conn, ctx.business_id, period)
    e["pay_date"] = iso(payroll.next_payroll_date(conn, ctx.business_id))
    e["items"] = [{k: i[k] for k in ("name", "employment_type", "gross", "deductions", "net", "employer_contrib", "attendance_recorded")}
                  for i in e["items"]]
    return e


def t_payroll_anomalies(conn, ctx, a):
    period = a.get("period") or month_key(payroll.next_payroll_date(conn, ctx.business_id))
    return {"period": period, "anomalies": payroll.estimate(conn, ctx.business_id, period)["anomalies"]}


def t_prepare_payroll(conn, ctx, a):
    period = a.get("period") or month_key(payroll.next_payroll_date(conn, ctx.business_id))
    run = payroll.prepare(conn, ctx.business_id, period, _actor(ctx, "HR & Payroll Agent"))
    return {"payroll_run_id": run["id"], "period": run["period"], "status": run["status"], "total_gross": run["total_gross"],
            "total_net": run["total_net"], "total_employer_cost": run["total_employer_cost"],
            "anomalies": run["anomalies"], "approval_id": (run.get("approval") or {}).get("id"), "note": run.get("note"),
            "payment": "No salaries are paid until the owner approves and records payment."}


def t_payroll_runs(conn, ctx, a):
    return {"runs": [{k: r[k] for k in ("id", "period", "status", "total_gross", "total_net", "total_employer_cost")}
                     for r in payroll.list_runs(conn, ctx.business_id)[:6]]}


# ---------------- Contract Agent ----------------
def _contract_brief(c):
    return {k: c.get(k) for k in ("id", "title", "category", "counterparty", "start_date", "end_date", "days_to_expiry", "value",
                                  "value_period", "payment_terms", "auto_renew", "notice_days", "notice_deadline",
                                  "days_to_notice_deadline", "renewal_status", "status", "suggestions")}


def t_contracts(conn, ctx, a):
    return {"contracts": [_contract_brief(c) for c in contracts.list_contracts(conn, ctx.business_id)],
            "disclaimer": contracts.DISCLAIMER}


def t_expiring(conn, ctx, a):
    return {"within_days": int(a.get("days") or 30),
            "contracts": [_contract_brief(c) for c in contracts.expiring(conn, ctx.business_id, int(a.get("days") or 30))]}


def t_propose_contract_action(conn, ctx, a):
    c = contracts.find_contract(conn, ctx.business_id, a["contract"])
    return contracts.propose_action(conn, ctx.business_id, c["id"], a["action"], _actor(ctx, "Contract Agent"),
                                    a.get("months"), a.get("note") or "")


def t_contract_details(conn, ctx, a):
    c = contracts.find_contract(conn, ctx.business_id, a["contract"])
    out = _contract_brief(c)
    out.update({"summary": c["summary"], "termination_clause": c["termination_clause"], "key_clauses": c["key_clauses"],
                "source_document": c.get("filename"), "disclaimer": contracts.DISCLAIMER})
    if c.get("document_id"):
        pages = contracts.document_pages(conn, ctx.business_id, c["document_id"])["pages"]
        out["document_pages"] = [{"page": i, "text": p[:2500]} for i, p in enumerate(pages, 1)][:6]
    return out


def t_search_contracts(conn, ctx, a):
    return {"keyword": a["keyword"], "results": contracts.search(conn, ctx.business_id, a["keyword"])[:8]}


# ---------------- Business Intelligence Agent ----------------
def t_snapshot(conn, ctx, a):
    return dashboard.briefing_facts(conn, ctx.business_id)


def t_recommendations(conn, ctx, a):
    insights.refresh(conn, ctx.business_id)
    return {"recommendations": [{k: r[k] for k in ("id", "title", "category", "priority", "description", "suggested_action",
                                                    "estimated_benefit", "confidence", "agent")}
                                for r in insights.top(conn, ctx.business_id, int(a.get("limit") or 8))]}


def t_affordability(conn, ctx, a):
    """Deterministic cross-department check: can current cash cover payroll + restocking + obligations?"""
    biz = ctx.business_id
    days = int(a.get("days") or 30)
    include_restock = a.get("include_restock", True)
    cash = finance.cash_balance(conn, biz)
    ob = finance.upcoming_obligations(conn, biz, days)
    payroll_total = money(sum(o["amount"] for o in ob if o["type"] == "Payroll"))
    bills = money(sum(o["amount"] for o in ob if o["type"] == "Supplier bill"))
    open_pos = money(sum(o["amount"] for o in ob if o["type"] == "Purchase order"))
    recurring = money(sum(o["amount"] for o in ob if o["type"] == "Recurring"))
    plan = procurement.restock_plan(conn, biz) if include_restock else {"estimated_total": 0, "items": []}
    restock = plan["estimated_total"]
    fc = finance.forecast(conn, biz, days)
    ar = finance.receivables(conn, biz)
    committed_out = money(payroll_total + bills + open_pos + recurring + restock)
    remaining = money(cash + fc["total_expected_receipts"] - committed_out)
    return {
        "horizon_days": days,
        "current_cash_balance": {"value": cash, "basis": "recorded"},
        "payroll_due": {"value": payroll_total, "basis": "estimate from employee records" if payroll_total else "none in window"},
        "supplier_bills_due": {"value": bills, "basis": "recorded invoices"},
        "open_purchase_orders_due": {"value": open_pos, "basis": "approved / awaiting-approval orders, due at ETA + payment terms"},
        "recurring_expenses_due": {"value": recurring, "basis": "scheduled"},
        "recommended_restock_cost": {"value": restock, "basis": "estimate from supplier quotes",
                                     "items": [{"product": i["product"], "supplier": i.get("supplier"), "cost": i.get("line_total")}
                                               for i in plan["items"] if not i.get("already_on_order")]},
        "expected_customer_receipts_not_overdue": {"value": fc["total_expected_receipts"], "basis": "recorded invoices"},
        "total_committed_outflows": committed_out,
        "projected_balance_after_commitments": {"value": remaining,
                                                "basis": "cash + receipts due - payroll - bills - open POs - recurring - new restock; excludes new sales"},
        "overdue_receivables_upside": {"value": ar["total_overdue"], "count": ar["overdue_count"],
                                       "basis": "not counted until collected"},
        "estimated_sales_inflow_over_horizon": {"value": money(fc["estimated_daily_sales_inflow"] * days),
                                                "basis": "28-day average, not counted above"},
        "affordable": remaining >= 0,
        "cushion_pct_of_obligations": round(remaining / committed_out * 100, 1) if committed_out else None,
    }


TOOLS: list[Tool] = [
    # inventory
    Tool("get_all_inventory", "inventory", "List all products with stock %, status, average daily usage, days of cover and stockout risk.",
         _obj(), t_get_all_inventory, "inventory.read", label="Reading inventory levels"),
    Tool("get_low_stock_products", "inventory", "Products below the configured low-stock threshold (default 20% of max capacity).",
         _obj(), t_get_low_stock, "inventory.read", label="Checking products below threshold"),
    Tool("get_inventory", "inventory", "Stock details for one product.", _obj({"product": PRODUCT_REF}, ["product"]),
         t_get_inventory, "inventory.read", label="Looking up product"),
    Tool("get_stock_history", "inventory", "Daily usage for the last 14 days and recent stock movements for a product.",
         _obj({"product": PRODUCT_REF}, ["product"]), t_stock_history, "inventory.read", label="Reading stock history"),
    Tool("forecast_product_demand", "inventory", "Moving-average demand forecast: avg daily sales, weekly demand, depletion date, stockout risk, quantity to refill.",
         _obj({"product": PRODUCT_REF}, ["product"]), t_forecast, "inventory.read", label="Forecasting demand"),
    Tool("get_supplier_for_product", "inventory", "Preferred supplier and all supplier quotes for a product.",
         _obj({"product": PRODUCT_REF}, ["product"]), t_supplier_for_product, "inventory.read", label="Finding suppliers"),
    # procurement
    Tool("get_suppliers", "procurement", "All suppliers with products, prices, MOQs, lead times, reliability and on-time rate.",
         _obj(), t_get_suppliers, "procurement.read", label="Loading suppliers"),
    Tool("compare_supplier_quotes", "procurement", "Compare suppliers for a product on price, lead time vs projected stockout, MOQ and reliability; returns a recommended supplier with rationale.",
         _obj({"product": PRODUCT_REF, "quantity": {"type": "number", "minimum": 1, "description": "Optional quantity; defaults to refill-to-capacity"}}, ["product"]),
         t_compare_quotes, "procurement.read", label="Comparing supplier quotations"),
    Tool("get_restock_plan", "procurement", "Restock plan for every low-stock product: recommended supplier, quantity (rounded to MOQ), cost and ETA, plus total estimated cost.",
         _obj(), t_restock_plan, "procurement.read", label="Building restock plan"),
    Tool("draft_restock_purchase_orders", "procurement", "ACTION: create purchase orders (one per supplier) for low-stock products, sized by the restock plan. Orders go to the approval queue; nothing is submitted until a human approves.",
         _obj({"products": {"type": "array", "items": {"type": "string"}, "description": "Optional subset of product names; default all low-stock products"}}),
         t_draft_restock_pos, "procurement.write", action=True, label="Drafting purchase orders"),
    Tool("create_purchase_order", "procurement", "ACTION: create one purchase order for a product from a named supplier. Goes to the approval queue.",
         _obj({"supplier": {"type": "string"}, "product": PRODUCT_REF, "quantity": {"type": "number", "minimum": 1},
               "note": {"type": "string"}}, ["supplier", "product", "quantity"]),
         t_create_po, "procurement.write", action=True, label="Creating purchase order"),
    Tool("get_purchase_orders", "procurement", "List purchase orders, optionally filtered by status.",
         _obj({"status": {"type": "string", "enum": ["DRAFT", "AWAITING_APPROVAL", "APPROVED", "SUBMITTED", "PARTIALLY_RECEIVED", "COMPLETED", "CANCELLED"]}}),
         t_get_pos, "procurement.read", label="Listing purchase orders"),
    Tool("get_supplier_performance", "procurement", "On-time delivery, spend and recent price changes for a supplier.",
         _obj({"supplier": {"type": "string"}}, ["supplier"]), t_supplier_performance, "procurement.read", label="Checking supplier performance"),
    # finance
    Tool("get_financial_summary", "finance", "Revenue, expenses, net operating profit, cash balance, receivables and payables. period: 'last30' (default) or 'YYYY-MM'.",
         _obj({"period": {"type": "string"}}), t_fin_summary, "finance.read", label="Retrieving financial summary"),
    Tool("get_transactions", "finance", "Transactions in a date range with totals by category.",
         _obj({"start_date": {"type": "string"}, "end_date": {"type": "string"}, "direction": {"type": "string", "enum": ["INCOME", "EXPENSE"]},
               "category": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 40}}),
         t_transactions, "finance.read", label="Reading transactions"),
    Tool("get_accounts_receivable", "finance", "Open customer invoices with days overdue.", _obj(), t_receivables, "finance.read",
         label="Checking customer invoices"),
    Tool("get_accounts_payable", "finance", "Open supplier bills with days until due.", _obj(), t_payables, "finance.read",
         label="Checking supplier bills"),
    Tool("forecast_cash_balance", "finance", "Cash-flow forecast with committed and expected scenarios, lowest balance and stated assumptions.",
         _obj({"days": {"type": "integer", "minimum": 7, "maximum": 120}}), t_forecast_cash, "finance.read", label="Forecasting cash flow"),
    Tool("get_upcoming_obligations", "finance", "Itemised upcoming payments (bills, recurring expenses, payroll) within N days.",
         _obj({"days": {"type": "integer", "minimum": 1, "maximum": 120}}), t_obligations, "finance.read", label="Listing upcoming obligations"),
    Tool("get_spending_anomalies", "finance", "Expense categories with unusual increases vs prior periods, and budget usage.",
         _obj(), t_anomalies, "finance.read", label="Scanning for unusual spending"),
    Tool("record_transaction", "finance", "ACTION: record an income or expense transaction the user explicitly described.",
         _obj({"direction": {"type": "string", "enum": ["INCOME", "EXPENSE"]}, "amount": {"type": "number", "minimum": 0.01},
               "description": {"type": "string"}, "category": {"type": "string"}, "date": {"type": "string"}, "counterparty": {"type": "string"}},
              ["direction", "amount", "description"]), t_record_txn, "finance.write", action=True, label="Recording transaction"),
    # hr
    Tool("get_employees", "hr", "Employee records (salary fields only if the user may see them).", _obj(), t_employees, "employees.read",
         label="Reading employee records"),
    Tool("estimate_payroll", "hr", "Deterministic payroll estimate for a period (default: next payroll): gross, deductions, net, employer cost, anomalies.",
         _obj({"period": {"type": "string", "description": "YYYY-MM"}}), t_estimate_payroll, "salary.read", label="Calculating payroll estimate"),
    Tool("identify_payroll_anomalies", "hr", "Missing timesheets, missing payment details, unusual changes and unverified rules.",
         _obj({"period": {"type": "string"}}), t_payroll_anomalies, "employees.read", label="Checking payroll data quality"),
    Tool("prepare_payroll", "hr", "ACTION: prepare the draft payroll run for a period and request owner approval. Does not pay anyone.",
         _obj({"period": {"type": "string"}}), t_prepare_payroll, "payroll.prepare", action=True, label="Preparing draft payroll"),
    Tool("get_payroll_runs", "hr", "Recent payroll runs and their status.", _obj(), t_payroll_runs, "salary.read", label="Listing payroll runs"),
    # contracts
    Tool("get_contracts", "contracts", "All contracts with dates, value, renewal terms and notice deadlines.", _obj(), t_contracts,
         "contracts.read", label="Loading contracts"),
    Tool("get_expiring_contracts", "contracts", "Contracts expiring, or with a non-renewal notice deadline, within N days.",
         _obj({"days": {"type": "integer", "minimum": 1, "maximum": 365}}), t_expiring, "contracts.read", label="Checking expiring contracts"),
    Tool("get_contract_details", "contracts", "Full details, key clauses and page text of one contract (for citing page numbers).",
         _obj({"contract": {"type": "string", "description": "Contract title, counterparty or id"}}, ["contract"]),
         t_contract_details, "contracts.read", label="Reading contract"),
    Tool("propose_contract_action", "contracts", "ACTION: ask the owner to approve a decision on a contract: EXTEND it (default: by its current term length, or `months`) or END_AT_EXPIRY (turn off auto-renewal and give notice). Goes to the approval queue; nothing changes until a human approves. Only use when the user asks to extend, renew, end or cancel a contract.",
         _obj({"contract": {"type": "string", "description": "Contract title, counterparty or id"},
               "action": {"type": "string", "enum": ["EXTEND", "END_AT_EXPIRY"]},
               "months": {"type": "integer", "minimum": 1, "maximum": 60},
               "note": {"type": "string"}}, ["contract", "action"]),
         t_propose_contract_action, "contracts.write", action=True, label="Requesting a contract decision"),
    Tool("search_contracts", "contracts", "Keyword search across contracts and their documents; returns page-level excerpts.",
         _obj({"keyword": {"type": "string"}}, ["keyword"]), t_search_contracts, "contracts.read", label="Searching contracts"),
    # bi
    Tool("get_business_health_snapshot", "bi", "Verified cross-department snapshot: sales, cash, low stock, payroll, receivables, payables, 30-day forecast, top recommendations.",
         _obj(), t_snapshot, "dashboard.read", label="Taking business health snapshot"),
    Tool("get_recommendations", "bi", "Current prioritised, data-backed recommendations from all detectors.",
         _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 15}}), t_recommendations, "insights.read", label="Gathering recommendations"),
    Tool("evaluate_affordability", "bi", "Deterministic check whether cash covers payroll, supplier bills, recurring expenses and the recommended restock within N days. Use this instead of doing arithmetic yourself.",
         _obj({"days": {"type": "integer", "minimum": 7, "maximum": 90}, "include_restock": {"type": "boolean"}}),
         t_affordability, "finance.read", label="Evaluating affordability"),
]

TOOL_MAP = {t.name: t for t in TOOLS}


def tools_for(agent: str, ctx) -> list[Tool]:
    return [t for t in TOOLS if t.agent == agent and ctx.can(t.perm)]


def execute(tool: Tool, ctx, raw_args) -> dict:
    """Validate arguments, check permission again, run the tool in its own DB connection."""
    if not ctx.can(tool.perm):
        raise ToolError(f"Permission denied: role {ctx.role} lacks '{tool.perm}'")
    args = raw_args
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args or "{}")
        except ValueError:
            raise ToolError("Arguments were not valid JSON")
    args = validate_args(tool.parameters, args)
    with db.connect() as conn:
        try:
            return tool.handler(conn, ctx, args)
        except ToolError:
            raise
        except Exception as e:  # business-rule violations come back as tool errors the agent can reason about
            msg = getattr(e, "message", None) or str(e)
            raise ToolError(msg)
