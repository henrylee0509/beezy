"""Seed the "Brew & Bean Cafe" demonstration business. Every seeded record is flagged is_demo=1
and named with "(Demo)" where it represents a third party. Dates are generated relative to today
so the demo always looks current."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from . import db
from .security import hash_password
from .services import contracts as contract_svc
from .services import insights, inventory
from .services.common import add_months, iso, money, month_bounds, month_key, today

DEMO_PASSWORD = "demo1234"
DEMO_USERS = [
    ("owner@brewbean.demo", "Mia Chen (Owner)", "OWNER"),
    ("manager@brewbean.demo", "Alex Tan (Manager)", "MANAGER"),
    ("hr@brewbean.demo", "Hana Ito (HR)", "HR"),
    ("finance@brewbean.demo", "Ravi Kumar (Finance)", "FINANCE"),
    ("staff@brewbean.demo", "Jordan Lee (Staff)", "STAFF"),
]

# name, sku, category, unit, current, max, unit_cost, recent_28d_units, prior_28d_units
PRODUCTS = [
    ("Coffee Beans", "BEAN-HOUSE-1KG", "Coffee", "bag (1kg)", 12, 100, 28.00, 84, 69),
    ("Fresh Milk", "MILK-FRESH-1L", "Dairy", "carton (1L)", 15, 100, 2.40, 168, 160),
    ("Oat Milk", "MILK-OAT-1L", "Dairy", "carton (1L)", 65, 100, 3.80, 70, 62),
    ("Bottled Water", "BEV-WATER-500", "Beverages", "bottle", 120, 500, 0.45, 392, 380),
    ("Paper Cups", "PKG-CUP-12OZ", "Packaging", "sleeve (50 cups)", 180, 500, 4.50, 168, 160),
    ("Sugar", "DRY-SUGAR-1KG", "Dry Goods", "kg", 250, 500, 1.80, 22, 24),
    ("Chocolate Syrup", "SYR-CHOC-750", "Syrups", "bottle (750ml)", 18, 100, 8.50, 36, 33),
]

# name, contact, email, phone, terms, reliability, preferred
SUPPLIERS = [
    ("Highland Roasters (Demo)", "Daniel Ong", "orders@highland-roasters.demo", "+00 6100 0001", "Net 14", 4.7, 1),
    ("Metro Dairy Co (Demo)", "Siti Rahman", "sales@metrodairy.demo", "+00 6100 0002", "Net 7", 4.5, 1),
    ("Harbour Wholesale (Demo)", "Kevin Lim", "trade@harbourwholesale.demo", "+00 6100 0003", "Net 30", 4.1, 0),
    ("QuickServe Supplies (Demo)", "Grace Ho", "hello@quickserve.demo", "+00 6100 0004", "Net 14", 4.3, 0),
]

# supplier index -> {product name: (unit_price, moq, lead_days, previous_price)}
CATALOG = {
    0: {"Coffee Beans": (28.00, 10, 3, 26.00), "Chocolate Syrup": (9.80, 12, 3, None)},
    1: {"Fresh Milk": (2.40, 12, 1, None), "Oat Milk": (3.80, 12, 1, None)},
    2: {"Coffee Beans": (25.50, 50, 6, None), "Fresh Milk": (2.20, 48, 3, None), "Chocolate Syrup": (8.50, 24, 5, None),
        "Bottled Water": (0.45, 120, 4, None), "Paper Cups": (4.20, 50, 4, None), "Sugar": (1.80, 25, 4, None),
        "Oat Milk": (3.60, 48, 4, None)},
    3: {"Chocolate Syrup": (9.20, 6, 2, None), "Paper Cups": (4.60, 20, 2, None), "Bottled Water": (0.50, 48, 1, None)},
}
PREFERRED = {"Coffee Beans": 0, "Fresh Milk": 1, "Oat Milk": 1, "Bottled Water": 2, "Paper Cups": 2, "Sugar": 2,
             "Chocolate Syrup": 2}

# code, name, title, dept, type, start, base, hourly, allowance, payment_details_on_file, current_hours, ot
EMPLOYEES = [
    ("E001", "Alex Tan", "Cafe Manager", "Operations", "MONTHLY", "2022-03-01", 3800, 0, 250, 1, 0, 6),
    ("E002", "Priya Raman", "Head Barista", "Front of House", "MONTHLY", "2023-01-09", 3000, 0, 150, 1, 0, 4),
    ("E003", "Jordan Lee", "Barista", "Front of House", "MONTHLY", "2024-06-17", 2500, 0, 0, 1, 0, 0),
    ("E004", "Sam Wong", "Part-time Barista", "Front of House", "HOURLY", "2025-02-03", 0, 12.50, 0, 1, None, 0),
    ("E005", "Nur Aisyah", "Kitchen Assistant", "Kitchen", "HOURLY", "2025-08-11", 0, 11.00, 0, 0, 110, 6),
]


def _contract_texts(t):
    sup_start = add_months(t, -11) - timedelta(days=t.day - 1)
    sup_end = t + timedelta(days=40)
    lease_start = add_months(t, -17).replace(day=1)
    lease_end = t + timedelta(days=200)
    emp_start = add_months(t, -10).replace(day=1)
    emp_end = t + timedelta(days=55)
    svc_start = add_months(t, -12) + timedelta(days=20)
    svc_end = t + timedelta(days=20)
    fmt = lambda d: d.strftime("%d %B %Y")
    return [
        {"title": "Coffee Supply Agreement — Highland Roasters", "category": "Supplier Agreement",
         "counterparty": "Highland Roasters (Demo)", "start": sup_start, "end": sup_end, "value": 2200, "value_period": "month",
         "payment_terms": "Net 14 days from invoice date", "auto_renew": 1, "notice_days": 30,
         "termination": "Either party may terminate for material breach with 14 days' written notice to remedy.",
         "pages": [
             f"COFFEE SUPPLY AGREEMENT (DEMONSTRATION DOCUMENT)\nThis Supply Agreement is made between Brew & Bean Cafe (the \"Buyer\") and Highland Roasters (Demo) (the \"Supplier\").\n1. TERM. This Agreement commences on {fmt(sup_start)} and expires on {fmt(sup_end)}.\n2. PRODUCTS. The Supplier shall supply roasted coffee beans in 1kg bags at the prices in Schedule A. Prices may be revised once per quarter with 30 days' notice.\n3. MINIMUM VOLUME. The Buyer intends to purchase approximately 80 bags per month.",
             f"4. PAYMENT. Payment terms are Net 14 days from invoice date. Late payments accrue interest at 1% per month.\n5. RENEWAL. This Agreement shall automatically renew for successive twelve (12) month periods unless either party gives at least 30 days' prior written notice of non-renewal before the expiry date.\n6. TERMINATION. Either party may terminate for material breach with 14 days' written notice to remedy.\n7. DELIVERY. Standard delivery lead time is 3 business days.\nSchedule A: House blend 1kg — 28.00 per bag (previously 26.00)."]},
        {"title": "Commercial Lease — Unit 01-12, Riverside Mall", "category": "Rental Agreement",
         "counterparty": "Riverside Mall Management (Demo)", "start": lease_start, "end": lease_end, "value": 6500, "value_period": "month",
         "payment_terms": "Rent payable monthly in advance on the 1st", "auto_renew": 0, "notice_days": 90,
         "termination": "Tenant may not terminate early except with 3 months' rent as compensation.",
         "pages": [
             f"LEASE AGREEMENT (DEMONSTRATION DOCUMENT)\nBetween Riverside Mall Management (Demo) (\"Landlord\") and Brew & Bean Cafe (\"Tenant\").\nPremises: Unit 01-12, Riverside Mall.\nTerm: commencing {fmt(lease_start)} and ending on {fmt(lease_end)}.\nRent: $6,500 per month payable in advance on the 1st of each month. Service charge included.",
             "Renewal: the Tenant may request renewal by giving 90 days' written notice before expiry; renewal is subject to the Landlord's agreement and revised rent.\nTermination: the Tenant may not terminate early except by paying 3 months' rent as compensation.\nSecurity deposit equal to two months' rent is held by the Landlord."]},
        {"title": "Fixed-term Employment Contract — Priya Raman", "category": "Employment Contract",
         "counterparty": "Priya Raman (Demo employee)", "start": emp_start, "end": emp_end, "value": 3000, "value_period": "month",
         "payment_terms": "Salary paid monthly on the 28th", "auto_renew": 0, "notice_days": 30,
         "termination": "Either party may terminate with 30 days' notice or salary in lieu.",
         "pages": [
             f"EMPLOYMENT CONTRACT (DEMONSTRATION DOCUMENT)\nEmployer: Brew & Bean Cafe. Employee: Priya Raman (fictional).\nPosition: Head Barista.\nTerm: fixed term from {fmt(emp_start)} until {fmt(emp_end)}.\nSalary: $3,000 per month plus a $150 monthly allowance, paid on the 28th.\nTermination: either party may terminate with 30 days' notice or salary in lieu."]},
        {"title": "Equipment Maintenance Service Agreement — CleanPro", "category": "Service Agreement",
         "counterparty": "CleanPro Equipment Services (Demo)", "start": svc_start, "end": svc_end, "value": 450, "value_period": "quarter",
         "payment_terms": "Quarterly in advance, Net 30", "auto_renew": 0, "notice_days": None,
         "termination": "Either party may terminate with 30 days' written notice.",
         "pages": [
             f"SERVICE AGREEMENT (DEMONSTRATION DOCUMENT)\nBetween CleanPro Equipment Services (Demo) and Brew & Bean Cafe.\nServices: quarterly preventive maintenance of espresso machines and grinders, with emergency call-outs within 24 hours.\nTerm: {fmt(svc_start)} to {fmt(svc_end)}.\nFees: $450 per quarter, invoiced quarterly in advance, payment terms Net 30.\nTermination: either party may terminate with 30 days' written notice."]},
    ]


def seed_demo(conn, name: str = "Brew & Bean Cafe", owner_user_id: int | None = None) -> int:
    rng = random.Random(42)
    t = today()
    with db.transaction(conn):
        biz = db.insert(conn, "businesses", {"name": name, "business_type": "Cafe / Retail F&B", "currency": "SGD",
                                             "jurisdiction": "GENERIC", "is_demo": 1})
        db.insert(conn, "business_settings", {"business_id": biz, "budgets": json.dumps({"Utilities": 1000, "Marketing": 800,
                                                                                          "Maintenance": 500})})
        # users
        for i, (email, uname, role) in enumerate(DEMO_USERS):
            u = db.one(conn, "SELECT id FROM users WHERE email=?", (email,))
            uid = u["id"] if u else db.insert(conn, "users", {"email": email, "name": uname, "password_hash": hash_password(DEMO_PASSWORD)})
            db.insert(conn, "business_members", {"business_id": biz, "user_id": uid, "role": role})
        if owner_user_id and not db.one(conn, "SELECT 1 FROM business_members WHERE business_id=? AND user_id=?", (biz, owner_user_id)):
            db.insert(conn, "business_members", {"business_id": biz, "user_id": owner_user_id, "role": "OWNER"})

        # suppliers & products
        sup_ids = [db.insert(conn, "suppliers", {"business_id": biz, "name": s[0], "contact_name": s[1], "email": s[2], "phone": s[3],
                                                 "payment_terms": s[4], "reliability": s[5], "is_preferred": s[6], "is_demo": 1})
                   for s in SUPPLIERS]
        pids = {}
        for (pname, sku, cat, unit, cur, mx, cost, recent, prior) in PRODUCTS:
            pids[pname] = db.insert(conn, "products", {
                "business_id": biz, "sku": sku, "name": pname, "category": cat, "unit": unit, "current_stock": cur,
                "max_stock": mx, "unit_cost": cost, "unit_price": 2.50 if pname == "Bottled Water" else 0,
                "preferred_supplier_id": sup_ids[PREFERRED[pname]], "is_demo": 1})
            # 56 days of consumption history whose window totals are exact (so forecasts are reproducible)
            for window_total, offset in ((recent, 0), (prior, 28)):
                weights = [rng.uniform(0.7, 1.3) * (1.25 if (t - timedelta(days=offset + d + 1)).weekday() >= 5 else 1)
                           for d in range(28)]
                scale = window_total / sum(weights)
                qty = [round(w * scale, 1) for w in weights]
                qty[0] = round(qty[0] + window_total - sum(qty), 1)
                for d in range(28):
                    if qty[d] > 0:
                        db.insert(conn, "inventory_movements", {"business_id": biz, "product_id": pids[pname], "kind": "sale",
                                                                "quantity": -qty[d], "note": "POS consumption (demo)",
                                                                "occurred_on": iso(t - timedelta(days=offset + d))})
        for si, items in CATALOG.items():
            for pname, (price, moq, lead, prev) in items.items():
                db.insert(conn, "supplier_products", {"business_id": biz, "supplier_id": sup_ids[si], "product_id": pids[pname],
                                                      "unit_price": price, "min_order_qty": moq, "lead_time_days": lead,
                                                      "previous_unit_price": prev})
        # a completed historic PO per main supplier (gives supplier performance data)
        for si, pname, qty, days_ago, on_time in ((0, "Coffee Beans", 80, 24, True), (1, "Fresh Milk", 120, 9, True),
                                                  (2, "Paper Cups", 200, 40, False)):
            price = CATALOG[si][pname][3] or CATALOG[si][pname][0]
            created = t - timedelta(days=days_ago)
            expected = created + timedelta(days=CATALOG[si][pname][2])
            received = expected if on_time else expected + timedelta(days=2)
            po = db.insert(conn, "purchase_orders", {
                "business_id": biz, "po_number": f"PO-{created.strftime('%y%m')}-{9000 + si}", "supplier_id": sup_ids[si],
                "status": "COMPLETED", "total": money(qty * price), "created_by": "Mia Chen (Owner)", "origin": "manual",
                "expected_date": iso(expected), "created_at": iso(created) + " 09:00:00", "approved_at": iso(created) + " 09:30:00",
                "approved_by": "Mia Chen (Owner)", "submitted_at": iso(created) + " 09:30:00", "received_at": iso(received) + " 10:00:00"})
            db.insert(conn, "purchase_order_items", {"po_id": po, "product_id": pids[pname], "quantity": qty, "unit_price": price,
                                                     "line_total": money(qty * price), "received_qty": qty})

        # finance
        history_start = add_months(t.replace(day=1), -5)
        acct = db.insert(conn, "financial_accounts", {"business_id": biz, "name": "Operating account (Demo)",
                                                      "opening_balance": 0, "opening_date": iso(history_start)})
        d = history_start
        while d < t:
            age = (t - d).days
            base = 1500 - age * 4.0  # growing cafe: steady upward revenue trend
            mult = 1.3 if d.weekday() >= 5 else 1.0
            amt = money(max(600, base * mult * rng.uniform(0.9, 1.1)))
            db.insert(conn, "transactions", {"business_id": biz, "direction": "INCOME", "category": "Sales", "amount": amt,
                                             "description": "Daily POS takings", "txn_date": iso(d), "payment_method": "Card / cash",
                                             "counterparty": "Walk-in customers", "is_demo": 1})
            if d.weekday() == 0:  # weekly supplier payments
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Supplier Payments",
                                                 "amount": money(rng.uniform(2300, 2900)), "description": "Weekly ingredient & packaging purchases",
                                                 "txn_date": iso(d), "counterparty": "Various suppliers (Demo)", "is_demo": 1})
            if d.day == 1:
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Rent", "amount": 6500,
                                                 "description": "Monthly rent — Unit 01-12", "txn_date": iso(d),
                                                 "counterparty": "Riverside Mall Management (Demo)", "is_demo": 1})
            if d.day == 10:
                util = 1060 if age <= 30 else money(rng.uniform(860, 900))
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Utilities", "amount": util,
                                                 "description": "Electricity & water bill", "txn_date": iso(d),
                                                 "counterparty": "City Utilities (Demo)", "is_demo": 1})
            if d.day == 15:
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Marketing", "amount": 600,
                                                 "description": "Social media ads", "txn_date": iso(d), "counterparty": "Ads platform (Demo)",
                                                 "is_demo": 1})
            if d.day == 5 and d.month % 3 == 0:
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Maintenance", "amount": 450,
                                                 "description": "Quarterly espresso machine maintenance", "txn_date": iso(d),
                                                 "counterparty": "CleanPro Equipment Services (Demo)", "is_demo": 1})
            if d.day == 20 and age < 70:
                db.insert(conn, "transactions", {"business_id": biz, "direction": "INCOME", "category": "Catering", "amount": money(rng.uniform(700, 1100)),
                                                 "description": "Corporate catering order (paid)", "txn_date": iso(d),
                                                 "counterparty": "Riverside Offices (Demo)", "is_demo": 1})
            d += timedelta(days=1)
        if t.day == 1:  # rent falls due today: record it as paid so the current month is consistent
            db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Rent", "amount": 6500,
                                             "description": "Monthly rent — Unit 01-12", "txn_date": iso(t.replace(day=1)),
                                             "counterparty": "Riverside Mall Management (Demo)", "is_demo": 1})
        for code, (rate_ee, rate_er) in (("pension", (5.0, 5.0)),):
            db.insert(conn, "payroll_rules", {"business_id": biz, "name": "Employee retirement contribution (demo rule)", "jurisdiction": "GENERIC",
                                              "kind": "EMPLOYEE_PCT", "rate": rate_ee, "applies_to": "ALL", "version": "demo-v1",
                                              "verified": 0, "source_note": "Illustrative rule for the demo. Configure your jurisdiction's verified rules."})
            db.insert(conn, "payroll_rules", {"business_id": biz, "name": "Employer retirement contribution (demo rule)", "jurisdiction": "GENERIC",
                                              "kind": "EMPLOYER_PCT", "rate": rate_er, "applies_to": "ALL", "version": "demo-v1",
                                              "verified": 0, "source_note": "Illustrative rule for the demo. Configure your jurisdiction's verified rules."})
        # employees, attendance and past payroll
        emp_ids = {}
        for (code, ename, title, dept, etype, start, base, hourly, allow, pay_ok, hours, ot) in EMPLOYEES:
            emp_ids[code] = db.insert(conn, "employees", {
                "business_id": biz, "emp_code": code, "name": ename, "job_title": title, "department": dept, "employment_type": etype,
                "start_date": start, "base_salary": base, "hourly_rate": hourly, "allowance": allow,
                "standard_hours": 44 if etype == "MONTHLY" else 20, "payment_details_on_file": pay_ok, "is_demo": 1})
            for back in range(0, 6):
                period = month_key(add_months(t.replace(day=1), -back))
                if back == 0 and hours is None:
                    continue  # missing timesheet for the current period -> payroll anomaly
                h = hours if back == 0 else (80 if code == "E004" else 104 if code == "E005" else 0)
                db.insert(conn, "attendance_records", {"business_id": biz, "employee_id": emp_ids[code], "period": period,
                                                       "hours_worked": h if etype == "HOURLY" else 0,
                                                       "overtime_hours": ot if back == 0 else rng.choice([0, 2, 4]),
                                                       "bonus": 0})
        db.insert(conn, "leave_records", {"business_id": biz, "employee_id": emp_ids["E003"], "start_date": iso(t + timedelta(days=8)),
                                          "end_date": iso(t + timedelta(days=9)), "days": 2, "leave_type": "Unpaid", "paid": 0})
        from .services import payroll as payroll_svc
        for back in (5, 4, 3, 2, 1):
            period = month_key(add_months(t.replace(day=1), -back))
            est = payroll_svc.estimate(conn, biz, period)
            pay_date = month_bounds(period)[0].replace(day=28)
            run = db.insert(conn, "payroll_runs", {"business_id": biz, "period": period, "status": "PAID", "total_gross": est["total_gross"],
                                                   "total_deductions": est["total_deductions"], "total_net": est["total_net"],
                                                   "total_employer_cost": est["total_employer_cost"], "anomalies": "[]",
                                                   "created_by": "Hana Ito (HR)", "approved_by": "Mia Chen (Owner)",
                                                   "approved_at": iso(pay_date), "paid_at": iso(pay_date)})
            for it in est["items"]:
                db.insert(conn, "payroll_items", {"run_id": run, "employee_id": it["employee_id"], "basic": it["basic"],
                                                  "allowances": it["allowances"], "overtime": it["overtime"], "bonus": it["bonus"],
                                                  "unpaid_leave_deduction": it["unpaid_leave_deduction"], "gross": it["gross"],
                                                  "deductions": it["deductions"], "net": it["net"], "employer_contrib": it["employer_contrib"],
                                                  "breakdown": json.dumps({"steps": it["steps"], "rules_applied": it["rules_applied"]})})
            if pay_date >= history_start:
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Salaries", "amount": est["total_gross"],
                                                 "description": f"Payroll {period} — gross salaries", "txn_date": iso(pay_date),
                                                 "counterparty": "Employees", "ref": f"PAYROLL-{period}", "is_demo": 1})
                db.insert(conn, "transactions", {"business_id": biz, "direction": "EXPENSE", "category": "Taxes & Statutory",
                                                 "amount": est["total_employer_cost"], "description": f"Payroll {period} — employer contributions",
                                                 "txn_date": iso(pay_date), "counterparty": "Statutory bodies", "ref": f"PAYROLL-{period}", "is_demo": 1})
        # recurring obligations
        for rname, cat, amt, dom in (("Rent — Unit 01-12", "Rent", 6500, 1), ("Electricity & water (est.)", "Utilities", 950, 10),
                                     ("Social media ads", "Marketing", 600, 15)):
            db.insert(conn, "recurring_expenses", {"business_id": biz, "name": rname, "category": cat, "amount": amt, "day_of_month": dom})
        # invoices
        for kind, num, cp, amt, issue_ago, due_offset in (
                ("RECEIVABLE", "INV-2041", "Northpoint Offices (Demo)", 1850.00, 78, -48),
                ("RECEIVABLE", "INV-2047", "Lakeside Events (Demo)", 2400.00, 65, -35),
                ("RECEIVABLE", "INV-2052", "Harbourview Co-working (Demo)", 960.00, 71, -41),
                ("RECEIVABLE", "INV-2063", "Riverside Offices (Demo)", 1200.00, 18, 12),
                ("PAYABLE", "HR-88412", "Highland Roasters (Demo)", 1680.00, 10, 4),
                ("PAYABLE", "MD-5521", "Metro Dairy Co (Demo)", 860.00, 1, 6),
                ("PAYABLE", "HW-30177", "Harbour Wholesale (Demo)", 2150.00, 12, 18)):
            db.insert(conn, "invoices", {"business_id": biz, "kind": kind, "number": num, "counterparty": cp, "amount": amt,
                                         "issue_date": iso(t - timedelta(days=issue_ago)), "due_date": iso(t + timedelta(days=due_offset)),
                                         "is_demo": 1})
        # set opening balance so today's cash balance is ~35k
        from .services import finance
        bal = finance.cash_balance(conn, biz)
        db.update(conn, "financial_accounts", acct, {"opening_balance": money(35240.55 - bal)}, biz)

        # contracts with source documents
        for c in _contract_texts(t):
            doc = contract_svc.store_document(conn, biz, c["title"] + " (Demo).txt", "text/plain", "", c["pages"], "system")
            cid = db.insert(conn, "contracts", {
                "business_id": biz, "document_id": doc, "title": c["title"], "category": c["category"], "counterparty": c["counterparty"],
                "start_date": iso(c["start"]), "end_date": iso(c["end"]), "value": c["value"], "value_period": c["value_period"],
                "payment_terms": c["payment_terms"], "auto_renew": c["auto_renew"], "notice_days": c["notice_days"],
                "termination_clause": c["termination"], "extraction_source": "demo-seed", "is_demo": 1,
                "summary": f"{c['category']} with {c['counterparty']} from {iso(c['start'])} to {iso(c['end'])}"
                           + (f"; auto-renews unless {c['notice_days']} days' notice is given" if c["auto_renew"] else "") + ".",
                "key_clauses": json.dumps([{"type": "Payment terms", "text": c["payment_terms"], "page": 1 if len(c["pages"]) == 1 else 2},
                                           {"type": "Termination", "text": c["termination"], "page": len(c["pages"])}]
                                          + ([{"type": "Auto-renewal", "text": f"Renews for 12 months unless {c['notice_days']} days' written notice is given.",
                                               "page": 2}] if c["auto_renew"] else []))})
            contract_svc.sync_reminders(conn, biz, cid)

        # automation rules
        rules = [
            ("Automatic Restock Preparation", "When stock drops below 20% of capacity: alert, forecast the stockout date and draft purchase orders for approval.",
             "STOCK_BELOW_PCT", {"pct": 20}, ["ALERT", "FORECAST", "DRAFT_PO"], 1),
            ("Critical Stock Escalation", "Escalate to the owner when stock falls below 5%.", "STOCK_CRITICAL", {"pct": 5}, ["ALERT"], 0),
            ("Overdue Invoice Follow-up", "Flag customer invoices more than 30 days overdue and draft a polite reminder.",
             "INVOICE_OVERDUE", {"days": 30}, ["ALERT", "DRAFT_REMINDER_EMAIL"], 0),
            ("Supplier Bill Due Soon", "Warn when supplier bills are due within 7 days.", "BILL_DUE_SOON", {"days": 7}, ["ALERT"], 0),
            ("Contract Renewal Reminders", "Remind 60/30/7 days before expiry and before auto-renewal notice deadlines.",
             "CONTRACT_EXPIRING", {}, ["REMINDER"], 0),
            ("Payroll Preparation", "Five days before payday, prepare draft payroll and request owner approval.",
             "PAYROLL_DUE", {"days_before": 5}, ["PREPARE_PAYROLL"], 1),
            ("Monthly Budget Watch", "Alert when a category reaches 90% of its monthly budget.", "EXPENSE_OVER_BUDGET", {}, ["ALERT"], 0),
            ("Late Delivery Watch", "Alert when a submitted purchase order passes its expected delivery date.", "DELIVERY_DELAYED", {}, ["ALERT"], 0),
        ]
        for rname, desc, trig, params, actions, appr in rules:
            db.insert(conn, "automation_rules", {"business_id": biz, "name": rname, "description": desc, "trigger_type": trig,
                                                 "params": json.dumps(params), "actions": json.dumps(actions), "approval_required": appr})
        # reminders whose date has already passed were "sent" before the demo starts
        conn.execute("UPDATE contract_reminders SET status='SENT' WHERE business_id=? AND remind_on<?", (biz, iso(t)))
        inventory.sync_alerts(conn, biz)
        insights.refresh(conn, biz)
    return biz


def ensure_demo(conn) -> int | None:
    row = db.one(conn, "SELECT id FROM businesses WHERE is_demo=1 ORDER BY id LIMIT 1")
    if row:
        return row["id"]
    return seed_demo(conn)


def reset_demo(conn, biz: int) -> int:
    b = db.one(conn, "SELECT * FROM businesses WHERE id=? AND is_demo=1", (biz,))
    if not b:
        raise ValueError("Only demo businesses can be reset")
    conn.execute("DELETE FROM businesses WHERE id=?", (biz,))
    return seed_demo(conn, b["name"])


if __name__ == "__main__":  # python -m backend.app.seed
    db.init_db()
    with db.connect() as c:
        print("Demo business id:", ensure_demo(c))
