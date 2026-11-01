"""Payroll: deterministic salary calculation with a configurable, versioned rules engine.

No LLM ever computes pay. Statutory rules are data (payroll_rules), each flagged verified/unverified;
the system never claims legal compliance for unverified rules.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from .. import db
from ..web import bad_request, forbidden, not_found
from .common import actor_type_for, audit, iso, money, month_bounds, month_key, now_iso, parse_date, today

OVERTIME_MULTIPLIER = 1.5

# Optional rule pack. Rates must be checked against the official source before use; they ship unverified.
RULE_TEMPLATES = {
    "SG_CPF_TEMPLATE": [
        {"name": "CPF employee contribution (age ≤55) — VERIFY", "jurisdiction": "SG", "kind": "EMPLOYEE_PCT",
         "rate": 20.0, "wage_ceiling": 8000, "applies_to": "ALL", "version": "template-2026",
         "source_note": "Template only. Confirm current rates and ceilings with the CPF Board before use.", "verified": 0},
        {"name": "CPF employer contribution (age ≤55) — VERIFY", "jurisdiction": "SG", "kind": "EMPLOYER_PCT",
         "rate": 17.0, "wage_ceiling": 8000, "applies_to": "ALL", "version": "template-2026",
         "source_note": "Template only. Confirm current rates and ceilings with the CPF Board before use.", "verified": 0},
        {"name": "Skills Development Levy — VERIFY", "jurisdiction": "SG", "kind": "EMPLOYER_PCT",
         "rate": 0.25, "wage_ceiling": 4500, "applies_to": "ALL", "version": "template-2026",
         "source_note": "Template only. Confirm with official guidance (minimum/maximum levy apply).", "verified": 0},
    ],
}


def working_days(period: str) -> int:
    y, m = map(int, period.split("-"))
    return sum(1 for d in range(1, calendar.monthrange(y, m)[1] + 1) if date(y, m, d).weekday() < 5)


def _unpaid_leave_days(conn, biz: int, emp_id: int, period: str) -> float:
    s, e = month_bounds(period)
    rows = db.all_(conn, """SELECT * FROM leave_records WHERE business_id=? AND employee_id=? AND paid=0
                            AND status='APPROVED' AND start_date<=? AND end_date>=?""", (biz, emp_id, iso(e), iso(s)))
    days = 0.0
    for r in rows:
        rs, re_ = max(parse_date(r["start_date"]), s), min(parse_date(r["end_date"]), e)
        span = (parse_date(r["end_date"]) - parse_date(r["start_date"])).days + 1
        overlap = (re_ - rs).days + 1
        days += r["days"] * overlap / span if span > 0 else 0
    return round(days, 2)


def active_rules(conn, biz: int) -> list[dict]:
    return db.all_(conn, "SELECT * FROM payroll_rules WHERE business_id=? AND active=1 ORDER BY kind, id", (biz,))


def calculate_employee(conn, biz: int, emp: dict, period: str, rules: list[dict] | None = None) -> dict:
    rules = rules if rules is not None else active_rules(conn, biz)
    att = db.one(conn, "SELECT * FROM attendance_records WHERE business_id=? AND employee_id=? AND period=?",
                 (biz, emp["id"], period))
    hours = att["hours_worked"] if att else 0.0
    ot_hours = att["overtime_hours"] if att else 0.0
    bonus = att["bonus"] if att else 0.0
    steps = []
    if emp["employment_type"] == "MONTHLY":
        basic = emp["base_salary"]
        hourly_equiv = emp["base_salary"] * 12 / (52 * emp["standard_hours"])
        overtime = ot_hours * hourly_equiv * OVERTIME_MULTIPLIER
        unpaid_days = _unpaid_leave_days(conn, biz, emp["id"], period)
        wd = working_days(period)
        unpaid = emp["base_salary"] / wd * unpaid_days
        steps.append(f"Basic monthly salary {basic:.2f}")
        if ot_hours:
            steps.append(f"Overtime {ot_hours:g}h × hourly equivalent {hourly_equiv:.2f} × {OVERTIME_MULTIPLIER} = {overtime:.2f}")
        if unpaid_days:
            steps.append(f"Unpaid leave {unpaid_days:g} of {wd} working days = -{unpaid:.2f}")
    else:
        basic = hours * emp["hourly_rate"]
        overtime = ot_hours * emp["hourly_rate"] * OVERTIME_MULTIPLIER
        unpaid = 0.0
        steps.append(f"{hours:g}h × {emp['hourly_rate']:.2f}/h = {basic:.2f}")
        if ot_hours:
            steps.append(f"Overtime {ot_hours:g}h × {emp['hourly_rate']:.2f} × {OVERTIME_MULTIPLIER} = {overtime:.2f}")
    allowances = emp["allowance"]
    if allowances:
        steps.append(f"Allowances {allowances:.2f}")
    if bonus:
        steps.append(f"Bonus {bonus:.2f}")
    gross = money(basic + allowances + overtime + bonus - unpaid)
    deductions, employer, applied = 0.0, 0.0, []
    for r in rules:
        if r["applies_to"] not in ("ALL", emp["employment_type"]):
            continue
        base = min(gross, r["wage_ceiling"]) if r["wage_ceiling"] else gross
        amt = money(base * r["rate"] / 100) if r["kind"].endswith("PCT") else money(r["rate"])
        if r["kind"].startswith("EMPLOYEE"):
            deductions += amt
        else:
            employer += amt
        applied.append({"rule_id": r["id"], "name": r["name"], "kind": r["kind"], "amount": amt,
                        "version": r["version"], "verified": bool(r["verified"])})
    net = money(gross - deductions)
    return {
        "employee_id": emp["id"], "emp_code": emp["emp_code"], "name": emp["name"], "job_title": emp["job_title"],
        "employment_type": emp["employment_type"], "period": period,
        "hours_worked": hours, "overtime_hours": ot_hours, "attendance_recorded": att is not None,
        "basic": money(basic), "allowances": money(allowances), "overtime": money(overtime), "bonus": money(bonus),
        "unpaid_leave_deduction": money(unpaid), "gross": gross, "deductions": money(deductions), "net": net,
        "employer_contrib": money(employer), "rules_applied": applied, "steps": steps,
    }


def anomalies_for(conn, biz: int, items: list[dict], employees: dict[int, dict], period: str, rules: list[dict]) -> list[dict]:
    out = []
    prev = db.one(conn, """SELECT * FROM payroll_runs WHERE business_id=? AND period<? AND status IN ('APPROVED','PAID')
                           ORDER BY period DESC LIMIT 1""", (biz, period))
    prev_items = {r["employee_id"]: r for r in db.all_(conn, "SELECT * FROM payroll_items WHERE run_id=?", (prev["id"],))} if prev else {}
    for it in items:
        e = employees[it["employee_id"]]
        if e["employment_type"] == "HOURLY" and not it["attendance_recorded"]:
            out.append({"severity": "HIGH", "employee": e["name"], "employee_id": e["id"],
                        "issue": f"No hours recorded for {period}; pay calculates to 0.00. Add attendance before approving."})
        if not e["payment_details_on_file"]:
            out.append({"severity": "HIGH", "employee": e["name"], "employee_id": e["id"],
                        "issue": "Bank/payment details missing — salary cannot be paid."})
        if it["overtime_hours"] > 30:
            out.append({"severity": "MEDIUM", "employee": e["name"], "employee_id": e["id"],
                        "issue": f"High overtime ({it['overtime_hours']:g}h) — please confirm."})
        p = prev_items.get(e["id"])
        if p and p["gross"] > 0 and it["attendance_recorded"] | (e["employment_type"] == "MONTHLY") and abs(it["gross"] - p["gross"]) / p["gross"] > 0.25:
            out.append({"severity": "MEDIUM", "employee": e["name"], "employee_id": e["id"],
                        "issue": f"Gross pay changed {((it['gross'] - p['gross']) / p['gross'] * 100):+.0f}% vs {prev['period']} ({p['gross']:.2f} → {it['gross']:.2f})."})
    unverified = [r["name"] for r in rules if not r["verified"]]
    if unverified:
        out.append({"severity": "INFO", "employee": None, "employee_id": None,
                    "issue": "Unverified payroll rules in use: " + "; ".join(unverified)
                             + ". Confirm with official sources or a professional before paying."})
    return out


def estimate(conn, biz: int, period: str | None = None) -> dict:
    period = period or month_key(today())
    rules = active_rules(conn, biz)
    emps = {e["id"]: e for e in db.all_(conn, "SELECT * FROM employees WHERE business_id=? AND status='ACTIVE'", (biz,))}
    items = [calculate_employee(conn, biz, e, period, rules) for e in emps.values()]
    return {
        "period": period, "employee_count": len(items), "items": items,
        "total_gross": money(sum(i["gross"] for i in items)),
        "total_deductions": money(sum(i["deductions"] for i in items)),
        "total_net": money(sum(i["net"] for i in items)),
        "total_employer_cost": money(sum(i["employer_contrib"] for i in items)),
        "total_cost_to_business": money(sum(i["gross"] + i["employer_contrib"] for i in items)),
        "anomalies": anomalies_for(conn, biz, items, emps, period, rules),
        "basis": "Deterministic backend calculation from employee records, attendance, leave and configured rules.",
    }


def prepare(conn, biz: int, period: str, actor: str) -> dict:
    from . import approvals
    try:
        month_bounds(period)
    except Exception:
        raise bad_request("period must be YYYY-MM")
    existing = db.one(conn, "SELECT * FROM payroll_runs WHERE business_id=? AND period=? AND status<>'CANCELLED'", (biz, period))
    if existing and existing["status"] in ("APPROVED", "PAID"):
        return {**get_run(conn, biz, existing["id"]), "note": f"Payroll for {period} is already {existing['status'].lower()}."}
    est = estimate(conn, biz, period)
    if not est["items"]:
        raise bad_request("No active employees")
    with db.transaction(conn):
        if existing:
            run_id = existing["id"]
            conn.execute("DELETE FROM payroll_items WHERE run_id=?", (run_id,))
            conn.execute("""UPDATE approval_requests SET status='CANCELLED' WHERE business_id=? AND kind='PAYROLL'
                            AND ref_id=? AND status='PENDING'""", (biz, run_id))
        else:
            run_id = db.insert(conn, "payroll_runs", {"business_id": biz, "period": period, "created_by": actor})
        db.update(conn, "payroll_runs", run_id, {
            "status": "AWAITING_APPROVAL", "total_gross": est["total_gross"], "total_deductions": est["total_deductions"],
            "total_net": est["total_net"], "total_employer_cost": est["total_employer_cost"],
            "anomalies": json.dumps(est["anomalies"]), "created_by": actor}, biz)
        for it in est["items"]:
            db.insert(conn, "payroll_items", {
                "run_id": run_id, "employee_id": it["employee_id"], "basic": it["basic"], "allowances": it["allowances"],
                "overtime": it["overtime"], "bonus": it["bonus"], "unpaid_leave_deduction": it["unpaid_leave_deduction"],
                "gross": it["gross"], "deductions": it["deductions"], "net": it["net"],
                "employer_contrib": it["employer_contrib"],
                "breakdown": json.dumps({"steps": it["steps"], "rules_applied": it["rules_applied"],
                                         "hours_worked": it["hours_worked"], "overtime_hours": it["overtime_hours"]})})
        high = sum(1 for a in est["anomalies"] if a["severity"] == "HIGH")
        approvals.create(conn, biz, "PAYROLL", run_id, f"Payroll {period} · {est['employee_count']} employees",
                         f"Gross {est['total_gross']:.2f}, net {est['total_net']:.2f}, employer cost {est['total_employer_cost']:.2f}"
                         + (f" · {high} issue(s) need attention" if high else ""),
                         est["total_gross"] + est["total_employer_cost"], actor, "OWNER")
        audit(conn, biz, actor_type_for(actor), actor, "payroll.prepare", "payroll_run", run_id,
              {"period": period, "total_gross": est["total_gross"], "anomalies": len(est["anomalies"])})
    return get_run(conn, biz, run_id)


def get_run(conn, biz: int, run_id: int) -> dict:
    run = db.get_owned(conn, "payroll_runs", run_id, biz)
    if not run:
        raise not_found("Payroll run")
    run["items"] = db.all_(conn, """SELECT pi.*, e.name, e.emp_code, e.job_title, e.employment_type FROM payroll_items pi
                                    JOIN employees e ON e.id=pi.employee_id WHERE pi.run_id=? ORDER BY e.emp_code""", (run_id,))
    run["approval"] = db.one(conn, """SELECT id, status, required_role FROM approval_requests WHERE business_id=?
                                      AND kind='PAYROLL' AND ref_id=? ORDER BY id DESC LIMIT 1""", (biz, run_id))
    return run


def list_runs(conn, biz: int) -> list[dict]:
    return db.all_(conn, "SELECT * FROM payroll_runs WHERE business_id=? ORDER BY period DESC, id DESC", (biz,))


def approve_run(conn, biz: int, run_id: int, actor: str, role: str) -> dict:
    if role not in ("OWNER", "SYSTEM"):
        raise forbidden("Only the owner can approve payroll")
    run = get_run(conn, biz, run_id)
    if run["status"] != "AWAITING_APPROVAL":
        raise bad_request(f"Payroll is {run['status']}")
    db.update(conn, "payroll_runs", run_id, {"status": "APPROVED", "approved_at": now_iso(), "approved_by": actor}, biz)
    audit(conn, biz, actor_type_for(actor), actor, "payroll.approve", "payroll_run", run_id, {"period": run["period"]})
    return get_run(conn, biz, run_id)


def record_payment(conn, biz: int, run_id: int, actor: str, role: str) -> dict:
    """Marks salaries as paid and books the expense. No real bank transfer is made (simulated)."""
    from . import finance
    if role not in ("OWNER", "SYSTEM"):
        raise forbidden("Only the owner can record salary payments")
    run = get_run(conn, biz, run_id)
    if run["status"] != "APPROVED":
        raise bad_request("Payroll must be approved before payment is recorded")
    with db.transaction(conn):
        db.update(conn, "payroll_runs", run_id, {"status": "PAID", "paid_at": now_iso()}, biz)
        finance.record_transaction(conn, biz, {
            "direction": "EXPENSE", "category": "Salaries", "amount": run["total_gross"],
            "description": f"Payroll {run['period']} — gross salaries (simulated payment)", "txn_date": iso(today()),
            "counterparty": "Employees", "ref": f"PAYROLL-{run['period']}"}, actor)
        if run["total_employer_cost"]:
            finance.record_transaction(conn, biz, {
                "direction": "EXPENSE", "category": "Taxes & Statutory", "amount": run["total_employer_cost"],
                "description": f"Payroll {run['period']} — employer contributions", "txn_date": iso(today()),
                "counterparty": "Statutory bodies", "ref": f"PAYROLL-{run['period']}"}, actor)
        audit(conn, biz, actor_type_for(actor), actor, "payroll.paid", "payroll_run", run_id,
              {"simulated": True, "total_gross": run["total_gross"]})
    return get_run(conn, biz, run_id)


def payslips(conn, biz: int, run_id: int) -> list[dict]:
    run = get_run(conn, biz, run_id)
    if run["status"] not in ("APPROVED", "PAID"):
        raise bad_request("Payslips are generated once payroll is approved")
    b = db.one(conn, "SELECT name, currency FROM businesses WHERE id=?", (biz,))
    return [{"business": b["name"], "currency": b["currency"], "period": run["period"], "status": run["status"],
             "employee": i["name"], "emp_code": i["emp_code"], "job_title": i["job_title"],
             "earnings": {"Basic": i["basic"], "Allowances": i["allowances"], "Overtime": i["overtime"], "Bonus": i["bonus"],
                          "Unpaid leave": -i["unpaid_leave_deduction"]},
             "gross": i["gross"], "deductions": i["breakdown"].get("rules_applied", []) if isinstance(i["breakdown"], dict) else [],
             "total_deductions": i["deductions"], "net": i["net"], "employer_contributions": i["employer_contrib"],
             "steps": i["breakdown"].get("steps", []) if isinstance(i["breakdown"], dict) else []}
            for i in run["items"]]


def next_payroll_date(conn, biz: int) -> date:
    from .common import settings
    s = settings(conn, biz)
    t = today()
    d = t.replace(day=min(s["payroll_day"], 28))
    if d < t:
        from .common import add_months
        d = add_months(t.replace(day=1), 1).replace(day=min(s["payroll_day"], 28))
    return d


def list_employees(conn, biz: int, include_salary: bool) -> list[dict]:
    rows = db.all_(conn, "SELECT * FROM employees WHERE business_id=? ORDER BY emp_code", (biz,))
    if not include_salary:
        for r in rows:
            for k in ("base_salary", "hourly_rate", "allowance"):
                r[k] = None
            r["salary_hidden"] = True
    return rows


def upsert_attendance(conn, biz: int, emp_id: int, period: str, hours: float, ot: float, bonus: float, actor: str) -> dict:
    if not db.get_owned(conn, "employees", emp_id, biz):
        raise not_found("Employee")
    conn.execute("""INSERT INTO attendance_records(business_id, employee_id, period, hours_worked, overtime_hours, bonus)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(employee_id, period) DO UPDATE SET hours_worked=excluded.hours_worked,
                    overtime_hours=excluded.overtime_hours, bonus=excluded.bonus""", (biz, emp_id, period, hours, ot, bonus))
    audit(conn, biz, actor_type_for(actor), actor, "attendance.record", "employee", emp_id,
          {"period": period, "hours": hours, "overtime": ot, "bonus": bonus})
    return db.one(conn, "SELECT * FROM attendance_records WHERE employee_id=? AND period=?", (emp_id, period))


def add_leave(conn, biz: int, emp_id: int, data: dict, actor: str) -> dict:
    if not db.get_owned(conn, "employees", emp_id, biz):
        raise not_found("Employee")
    s, e = parse_date(data["start_date"]), parse_date(data["end_date"])
    if not s or not e or e < s:
        raise bad_request("Invalid leave dates")
    lid = db.insert(conn, "leave_records", {"business_id": biz, "employee_id": emp_id, **data})
    audit(conn, biz, actor_type_for(actor), actor, "leave.record", "employee", emp_id, data)
    return db.get_owned(conn, "leave_records", lid, biz)
