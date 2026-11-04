"""Deterministic business logic: thresholds, forecasting, procurement, finance, payroll, contracts, automations."""
import pytest

from backend.app import db
from backend.app.services import (approvals, automations, contracts, finance, insights, inventory, payroll,
                                  procurement)
from backend.app.web import ApiError


def pid(conn, biz, name):
    return db.one(conn, "SELECT id FROM products WHERE business_id=? AND name=?", (biz, name))["id"]


# ---------------- inventory
def test_stock_percentage_formula():
    assert inventory.stock_pct(85, 500) == 17.0
    assert inventory.stock_pct(12, 100) == 12.0


def test_threshold_levels():
    s = {"low_stock_pct": 20, "urgent_stock_pct": 10, "critical_stock_pct": 5}
    assert inventory.level_for(85, 500, s) == "LOW"
    assert inventory.level_for(9, 100, s) == "URGENT"
    assert inventory.level_for(4, 100, s) == "CRITICAL"
    assert inventory.level_for(0, 100, s) == "OUT"
    assert inventory.level_for(20, 100, s) == "HEALTHY"


def test_demo_low_stock_detection(conn, biz):
    names = sorted(p["name"] for p in inventory.low_stock_products(conn, biz))
    assert names == ["Chocolate Syrup", "Coffee Beans", "Fresh Milk"]
    assert len(inventory.open_alerts(conn, biz)) == 3


def test_forecast_is_reproducible(conn, biz):
    f = inventory.forecast(conn, biz, pid(conn, biz, "Coffee Beans"))
    assert f["avg_daily_sales"] == 3.0          # 84 units over a 28-day window
    assert f["days_of_cover"] == 4.0             # 12 / 3
    assert f["demand_trend_pct"] == pytest.approx(21.7, abs=0.1)
    assert f["quantity_to_refill"] == 88


def test_adjust_stock_creates_alert_and_blocks_negative(conn, biz):
    oat = pid(conn, biz, "Oat Milk")
    p = inventory.adjust_stock(conn, biz, oat, -50, "sale", "test", "tester")
    assert p["level"] == "LOW" and p["stock_pct"] == 15.0
    assert any(a["product"] == "Oat Milk" for a in inventory.open_alerts(conn, biz))
    with pytest.raises(ApiError):
        inventory.adjust_stock(conn, biz, oat, -999, "sale", "too much", "tester")


# ---------------- procurement
def test_supplier_comparison_prefers_in_time_supplier(conn, biz):
    c = procurement.compare_quotes(conn, biz, pid(conn, biz, "Coffee Beans"))
    assert c["recommended"]["supplier"] == "Highland Roasters (Demo)"   # cheaper Harbour arrives after stockout
    cheapest = min(c["quotes"], key=lambda q: q["unit_price"])
    assert cheapest["supplier"] == "Harbour Wholesale (Demo)" and not cheapest["arrives_before_stockout"]


def test_restock_quantities_respect_moq(conn, biz):
    plan = {l["product"]: l for l in procurement.restock_plan(conn, biz)["items"]}
    assert plan["Coffee Beans"]["quantity"] == 90        # need 88, MOQ 10
    assert plan["Fresh Milk"]["quantity"] == 84          # need 85, MOQ 12; 96 would overfill >110%
    assert plan["Chocolate Syrup"]["supplier"] == "Harbour Wholesale (Demo)"
    assert procurement.restock_plan(conn, biz)["estimated_total"] == pytest.approx(3333.60)


def test_draft_orders_need_approval_and_skip_duplicates(conn, biz):
    r = procurement.draft_restock_orders(conn, biz, "agent:test")
    assert len(r["created"]) == 3 and all(c["status"] == "AWAITING_APPROVAL" for c in r["created"])
    again = procurement.draft_restock_orders(conn, biz, "agent:test")
    assert again["created"] == [] and len(again["skipped"]) == 3


def test_spending_limits_enforced(conn, biz):
    beans = pid(conn, biz, "Coffee Beans")
    sup = db.one(conn, "SELECT id FROM suppliers WHERE business_id=? AND name LIKE 'Highland%'", (biz,))["id"]
    po = procurement.create_po(conn, biz, sup, [{"product_id": beans, "quantity": 90}], "tester")
    assert po["total"] == 2520 and po["approval"]["required_role"] == "OWNER"
    with pytest.raises(ApiError) as e:
        approvals.decide(conn, biz, po["approval"]["id"], True, "Manager", "MANAGER")
    assert e.value.status == 403
    out = approvals.decide(conn, biz, po["approval"]["id"], True, "Owner", "OWNER")
    assert out["result"]["status"] == "SUBMITTED"


def test_moq_validation(conn, biz):
    beans = pid(conn, biz, "Coffee Beans")
    sup = db.one(conn, "SELECT id FROM suppliers WHERE business_id=? AND name LIKE 'Harbour%'", (biz,))["id"]
    with pytest.raises(ApiError):
        procurement.create_po(conn, biz, sup, [{"product_id": beans, "quantity": 10}], "tester")


def test_receiving_updates_stock_and_payables(conn, biz):
    milk = pid(conn, biz, "Fresh Milk")
    sup = db.one(conn, "SELECT id FROM suppliers WHERE business_id=? AND name LIKE 'Metro%'", (biz,))["id"]
    po = procurement.create_po(conn, biz, sup, [{"product_id": milk, "quantity": 60}], "tester")
    procurement.approve_and_submit(conn, biz, po["id"], "Owner", "OWNER")
    before = finance.payables(conn, biz)["total_open"]
    done = procurement.receive_po(conn, biz, po["id"], "tester")
    assert done["status"] == "COMPLETED"
    assert inventory.get_product(conn, biz, milk)["current_stock"] == 75
    assert finance.payables(conn, biz)["total_open"] == pytest.approx(before + 144.0)
    assert not any(a["product"] == "Fresh Milk" for a in inventory.open_alerts(conn, biz))


def test_auto_approval_only_when_enabled(conn, biz):
    conn.execute("UPDATE business_settings SET auto_po_enabled=1, auto_approve_limit=700 WHERE business_id=?", (biz,))
    r = procurement.draft_restock_orders(conn, biz, "automation:test", origin="automation")
    statuses = {c["supplier"]: c["status"] for c in r["created"]}
    assert statuses["Metro Dairy Co (Demo)"] == "SUBMITTED"         # 201.60 within limit
    assert statuses["Highland Roasters (Demo)"] == "AWAITING_APPROVAL"  # 2,520 above limit


# ---------------- finance
def test_cash_balance_and_definitions(conn, biz):
    s = finance.summary(conn, biz)
    assert s["cash_balance"] == pytest.approx(35240.55)
    assert s["net_operating_profit"] == pytest.approx(s["revenue"] - s["expenses"])
    assert s["overdue_invoice_count"] == 3 and s["overdue_receivables"] == 5210


def test_record_transaction_changes_cash(conn, biz):
    before = finance.cash_balance(conn, biz)
    t = finance.record_transaction(conn, biz, {"direction": "EXPENSE", "amount": 100, "description": "Electricity bill",
                                               "txn_date": finance.iso(finance.today())}, "tester")
    assert t["category"] == "Utilities"
    assert finance.cash_balance(conn, biz) == pytest.approx(before - 100)


def test_cash_forecast_scenarios(conn, biz):
    f = finance.forecast(conn, biz, 30)
    assert f["projected_committed_balance"] == pytest.approx(f["starting_cash"] + f["total_expected_receipts"] - f["total_obligations"], abs=0.02)
    assert any(o["type"] == "Payroll" for o in f["obligations"])
    assert f["overdue_receivables_excluded"] == 5210


def test_spending_anomaly_detects_utilities(conn, biz):
    cats = {a["category"] for a in finance.spending_anomalies(conn, biz)}
    assert "Utilities" in cats


# ---------------- payroll
def test_payroll_example_calculation(conn, biz):
    eid = db.insert(conn, "employees", {"business_id": biz, "emp_code": "X1", "name": "Example", "employment_type": "MONTHLY",
                                        "base_salary": 3000, "allowance": 200, "standard_hours": 44})
    db.insert(conn, "attendance_records", {"business_id": biz, "employee_id": eid, "period": "2030-01", "bonus": 150})
    rules = [{"id": 1, "name": "Fixed deduction", "kind": "EMPLOYEE_FIXED", "rate": 100, "wage_ceiling": None, "applies_to": "ALL",
              "version": "t", "verified": 1}]
    emp = db.get_owned(conn, "employees", eid, biz)
    r = payroll.calculate_employee(conn, biz, emp, "2030-01", rules)
    assert r["gross"] == 3350 and r["net"] == 3250


def test_hourly_and_overtime(conn, biz):
    emp = db.one(conn, "SELECT * FROM employees WHERE business_id=? AND emp_code='E005'", (biz,))
    period = db.one(conn, "SELECT MAX(period) p FROM attendance_records WHERE employee_id=?", (emp["id"],))["p"]
    r = payroll.calculate_employee(conn, biz, emp, period, [])
    assert r["basic"] == 1210 and r["overtime"] == 99.0 and r["gross"] == 1309


def test_payroll_anomalies_and_workflow(conn, biz):
    period = payroll.month_key(payroll.next_payroll_date(conn, biz))
    est = payroll.estimate(conn, biz, period)
    issues = {(a["employee"], a["severity"]) for a in est["anomalies"]}
    assert ("Sam Wong", "HIGH") in issues and ("Nur Aisyah", "HIGH") in issues
    run = payroll.prepare(conn, biz, period, "agent:HR")
    assert run["status"] == "AWAITING_APPROVAL" and run["approval"]["required_role"] == "OWNER"
    with pytest.raises(ApiError):
        payroll.record_payment(conn, biz, run["id"], "Owner", "OWNER")       # must approve first
    with pytest.raises(ApiError):
        approvals.decide(conn, biz, run["approval"]["id"], True, "Manager", "MANAGER")
    approvals.decide(conn, biz, run["approval"]["id"], True, "Owner", "OWNER")
    paid = payroll.record_payment(conn, biz, run["id"], "Owner", "OWNER")
    assert paid["status"] == "PAID"
    assert len(payroll.payslips(conn, biz, run["id"])) == 5


# ---------------- contracts
def test_heuristic_contract_extraction():
    pages = ["SUPPLY AGREEMENT between Brew & Bean Cafe and FreshBake Pastries (Demo) (\"Supplier\"). "
             "This Agreement commences on 1 November 2026 and expires on 31 October 2027.",
             "Payment terms: Net 30 days. This Agreement shall automatically renew for 12 months unless 45 days' written notice is given. "
             "Termination: either party may terminate with 30 days notice for breach."]
    m = contracts.validate_extraction(contracts.heuristic_extract(pages, "freshbake.pdf"))
    assert m["category"] == "Supplier Agreement"
    assert m["start_date"] == "2026-11-01" and m["end_date"] == "2027-10-31"
    assert m["auto_renew"] and m["notice_days"] == 45
    assert any(c["page"] == 2 for c in m["key_clauses"])


def test_contract_notice_deadline_and_reminders(conn, biz):
    cs = {c["title"]: c for c in contracts.list_contracts(conn, biz)}
    sup = cs["Coffee Supply Agreement — Highland Roasters"]
    assert sup["days_to_expiry"] == 40 and sup["days_to_notice_deadline"] == 10
    assert any(c["id"] == sup["id"] for c in contracts.expiring(conn, biz, 30))
    labels = {r["label"] for r in contracts.get_contract(conn, biz, sup["id"])["reminders"]}
    assert {"30 days before expiry", "Last day to give non-renewal notice"} <= labels


def test_validate_extraction_rejects_bad_dates():
    m = contracts.validate_extraction({"start_date": "2027-01-01", "end_date": "2026-01-01", "category": "nonsense", "value": "abc"})
    assert m["end_date"] is None and m["category"] == "Other" and m["value"] is None


# ---------------- automations & insights
def test_stock_automation_drafts_orders_with_audit(conn, biz):
    rule = db.one(conn, "SELECT * FROM automation_rules WHERE business_id=? AND trigger_type='STOCK_BELOW_PCT'", (biz,))
    res = automations.run_one(conn, biz, rule["id"])
    assert res["status"] == "TRIGGERED"
    assert sum(1 for d in res["details"] if d["type"] == "purchase_order") == 3
    assert len(approvals.list_(conn, biz)) == 3
    assert db.scalar(conn, "SELECT COUNT(*) FROM audit_logs WHERE business_id=? AND actor_type='AUTOMATION'", (biz,)) >= 1


def test_payroll_automation_respects_window(conn, biz):
    rule = db.one(conn, "SELECT * FROM automation_rules WHERE business_id=? AND trigger_type='PAYROLL_DUE'", (biz,))
    days = (payroll.next_payroll_date(conn, biz) - payroll.today()).days
    res = automations.run_one(conn, biz, rule["id"])
    assert (res["status"] == "TRIGGERED") == (days <= 5)


def test_insights_are_backed_by_data(conn, biz):
    recs = insights.list_(conn, biz)
    titles = {r["title"] for r in recs}
    assert "Restock Coffee Beans" in titles and "Collect 3 overdue customer invoices" in titles
    assert all(r["supporting_data"] for r in recs)
    procurement.draft_restock_orders(conn, biz, "agent:test")
    insights.refresh(conn, biz)
    beans = [r for r in insights.list_(conn, biz) if r["title"] == "Restock Coffee Beans"][0]
    assert beans["status"] == "COMPLETED"   # auto-resolved once on order


# ---------------- contract decisions (extend / end at expiry) go through approvals
def _coffee(conn, biz):
    return contracts.find_contract(conn, biz, "Coffee Supply")


def test_contract_suggestions_offer_extend_end_and_renegotiate(conn, biz):
    c = _coffee(conn, biz)
    acts = [o["action"] for o in c["suggestions"]]
    assert acts == ["EXTEND", "END_AT_EXPIRY", "RENEGOTIATE"]
    assert [o["action"] for o in c["suggestions"] if o["recommended"]] == ["EXTEND"]      # a supplier deal: keep it


def test_extend_contract_waits_for_approval_then_moves_end_date(conn, biz):
    c = _coffee(conn, biz)
    r = contracts.propose_action(conn, biz, c["id"], "EXTEND", "user:owner", months=12)
    assert contracts.get_contract(conn, biz, c["id"])["end_date"] == c["end_date"]       # nothing changes yet
    assert any(o.get("pending") for o in contracts.get_contract(conn, biz, c["id"])["suggestions"])
    approvals.decide(conn, biz, r["approval_id"], True, "user:owner", "OWNER")
    after = contracts.get_contract(conn, biz, c["id"])
    assert after["end_date"] > c["end_date"] and after["end_date"][:4] == str(int(c["end_date"][:4]) + 1)


def test_end_at_expiry_turns_off_auto_renew_after_approval(conn, biz):
    c = _coffee(conn, biz)
    r = contracts.propose_action(conn, biz, c["id"], "END_AT_EXPIRY", "user:owner")
    with pytest.raises(ApiError):
        approvals.decide(conn, biz, r["approval_id"], True, "user:manager", "MANAGER")    # owner-only decision
    approvals.decide(conn, biz, r["approval_id"], True, "user:owner", "OWNER")
    after = contracts.get_contract(conn, biz, c["id"])
    assert after["auto_renew"] == 0 and after["status"] == "ENDING"
