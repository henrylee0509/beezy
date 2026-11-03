"""Report generation with CSV and PDF export."""
from __future__ import annotations

import csv
import io
import json

from .. import db
from ..web import bad_request, not_found
from . import finance, insights, inventory, payroll, procurement
from .common import add_months, audit, actor_type_for, iso, month_bounds, month_key, today

KINDS = {
    "FINANCIAL": "Monthly financial report",
    "INVENTORY": "Inventory report",
    "PAYROLL": "Payroll summary",
    "SUPPLIER": "Supplier report",
    "PERFORMANCE": "Business performance & management summary",
}


def build(conn, biz: int, kind: str, period: str | None = None) -> dict:
    t = today()
    period = period or month_key(t)
    if kind == "FINANCIAL":
        sm = finance.summary(conn, biz, period)
        s, e = month_bounds(period)
        return {"kpis": {k: sm[k] for k in ("revenue", "expenses", "net_operating_profit", "cash_balance",
                                             "accounts_receivable", "accounts_payable")},
                "table": {"columns": ["Category", "Direction", "Total", "Count"],
                          "rows": [[r["category"], "Income", r["total"], r["count"]] for r in finance.category_breakdown(conn, biz, "INCOME", s, e)]
                          + [[r["category"], "Expense", r["total"], r["count"]] for r in finance.category_breakdown(conn, biz, "EXPENSE", s, e)]},
                "notes": [sm["basis"]]}
    if kind == "INVENTORY":
        ps = inventory.list_products(conn, biz)
        return {"kpis": {"inventory_value": inventory.inventory_value(conn, biz),
                         "products": len(ps), "below_threshold": sum(1 for p in ps if p["level"] != "HEALTHY")},
                "table": {"columns": ["Product", "SKU", "Stock", "Capacity", "Stock %", "Status", "Avg daily sales", "Days of cover", "Value"],
                          "rows": [[p["name"], p["sku"], p["current_stock"], p["max_stock"], p["stock_pct"], p["status_label"],
                                    p["avg_daily_sales"], p["days_of_cover"], p["stock_value"]] for p in ps]},
                "notes": ["Forecasts use a moving average of recorded sales."]}
    if kind == "PAYROLL":
        est = payroll.estimate(conn, biz, period)
        return {"kpis": {k: est[k] for k in ("total_gross", "total_deductions", "total_net", "total_employer_cost", "employee_count")},
                "table": {"columns": ["Employee", "Type", "Basic", "Allowances", "Overtime", "Gross", "Deductions", "Net", "Employer"],
                          "rows": [[i["name"], i["employment_type"], i["basic"], i["allowances"], i["overtime"], i["gross"],
                                    i["deductions"], i["net"], i["employer_contrib"]] for i in est["items"]]},
                "notes": [est["basis"]] + [a["issue"] for a in est["anomalies"]]}
    if kind == "SUPPLIER":
        sups = procurement.list_suppliers(conn, biz)
        return {"kpis": {"suppliers": len(sups), "total_spend_completed_orders": round(sum(s["performance"]["total_spend"] for s in sups), 2)},
                "table": {"columns": ["Supplier", "Preferred", "Terms", "Reliability", "Products", "Completed POs", "On-time %", "Spend"],
                          "rows": [[s["name"], "Yes" if s["is_preferred"] else "", s["payment_terms"], s["reliability"], len(s["products"]),
                                    s["performance"]["completed_orders"], s["performance"]["on_time_rate_pct"], s["performance"]["total_spend"]]
                                   for s in sups]},
                "notes": [f"Price change: {c['supplier']} {c['product']} {c['change_pct']}%" for c in procurement.price_changes(conn, biz)]}
    if kind == "PERFORMANCE":
        series = finance.monthly_series(conn, biz, 6)
        recs = insights.top(conn, biz, 8)
        return {"kpis": {k: v for k, v in finance.summary(conn, biz).items() if k in ("revenue", "expenses", "net_operating_profit", "cash_balance")},
                "table": {"columns": ["Month", "Revenue", "Expenses", "Net operating profit"],
                          "rows": [[m["month"] + (" (MTD)" if m["partial"] else ""), m["revenue"], m["expenses"], m["net_operating_profit"]] for m in series]},
                "recommendations": [{"title": r["title"], "priority": r["priority"], "action": r["suggested_action"]} for r in recs],
                "notes": ["Net operating profit is cash-basis revenue minus expenses."]}
    raise bad_request("Unknown report kind")


def generate(conn, biz: int, kind: str, period: str | None, actor: str, narrative: str | None = None,
             narrative_source: str | None = None) -> dict:
    if kind not in KINDS:
        raise bad_request("Unknown report kind")
    period = period or month_key(today())
    data = build(conn, biz, kind, period)
    rid = db.insert(conn, "reports", {"business_id": biz, "kind": kind, "title": f"{KINDS[kind]} — {period}", "period": period,
                                      "data": json.dumps(data, default=str), "narrative": narrative,
                                      "narrative_source": narrative_source, "created_by": actor})
    audit(conn, biz, actor_type_for(actor), actor, "report.generate", "report", rid, {"kind": kind, "period": period})
    return get(conn, biz, rid)


def get(conn, biz: int, rid: int) -> dict:
    r = db.get_owned(conn, "reports", rid, biz)
    if not r:
        raise not_found("Report")
    return r


def list_(conn, biz: int) -> list[dict]:
    return db.all_(conn, "SELECT id, kind, title, period, narrative_source, created_by, created_at FROM reports WHERE business_id=? ORDER BY id DESC LIMIT 100", (biz,))


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([report["title"]])
    for k, v in (report["data"].get("kpis") or {}).items():
        w.writerow([k, v])
    w.writerow([])
    tbl = report["data"].get("table") or {}
    w.writerow(tbl.get("columns", []))
    for row in tbl.get("rows", []):
        w.writerow(row)
    return buf.getvalue()


def to_pdf(report: dict, business_name: str, currency: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36,
                            title=report["title"])
    ss = getSampleStyleSheet()
    navy = colors.HexColor("#0f1f3d")
    els = [Paragraph(f"<font color='#0f766e'>Beezy</font> · {business_name}", ss["Normal"]),
           Paragraph(report["title"], ss["Title"]), Paragraph(f"Generated {report['created_at']} · Currency {currency}", ss["Normal"]),
           Spacer(1, 10)]
    kpis = report["data"].get("kpis") or {}
    if kpis:
        t = Table([[k.replace("_", " ").title() for k in kpis], [f"{v:,.2f}" if isinstance(v, float) else str(v) for v in kpis.values()]])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")), ("TEXTCOLOR", (0, 0), (-1, -1), navy),
                               ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1"))]))
        els += [t, Spacer(1, 12)]
    if report.get("narrative"):
        els += [Paragraph("Management summary" + (f" ({report['narrative_source']})" if report.get("narrative_source") else ""), ss["Heading3"])]
        for para in report["narrative"].split("\n"):
            if para.strip():
                els.append(Paragraph(para.replace("&", "&amp;").replace("<", "&lt;").replace("**", ""), ss["Normal"]))
        els.append(Spacer(1, 10))
    tbl = report["data"].get("table") or {}
    if tbl.get("rows"):
        rows = [tbl["columns"]] + [[f"{c:,.2f}" if isinstance(c, float) else ("" if c is None else str(c)) for c in r] for r in tbl["rows"]]
        t = Table(rows, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("FONTSIZE", (0, 0), (-1, -1), 8), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1"))]))
        els.append(t)
    for r in report["data"].get("recommendations") or []:
        els.append(Paragraph(f"[{r['priority']}] {r['title']} — {r['action']}", ss["Normal"]))
    notes = report["data"].get("notes") or []
    if notes:
        els += [Spacer(1, 10)] + [Paragraph(f"<font size=7 color='#64748b'>{n}</font>", ss["Normal"]) for n in notes]
    doc.build(els)
    return buf.getvalue()
