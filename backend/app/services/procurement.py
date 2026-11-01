"""Suppliers, quote comparison, purchase orders and goods receipt."""
from __future__ import annotations

import math
import re
from datetime import timedelta

from .. import db
from ..web import ApiError, bad_request, forbidden, not_found
from . import inventory
from .common import actor_type_for, audit, iso, money, now_iso, parse_date, settings, today

OPEN_PO_STATUSES = ("DRAFT", "AWAITING_APPROVAL", "APPROVED", "SUBMITTED", "PARTIALLY_RECEIVED")


def list_suppliers(conn, biz: int) -> list[dict]:
    sups = db.all_(conn, "SELECT * FROM suppliers WHERE business_id=? ORDER BY is_preferred DESC, name", (biz,))
    for s in sups:
        s["products"] = db.all_(conn, """SELECT sp.*, p.name AS product, p.unit FROM supplier_products sp
                                         JOIN products p ON p.id = sp.product_id
                                         WHERE sp.business_id=? AND sp.supplier_id=? AND p.active=1
                                         ORDER BY p.name""", (biz, s["id"]))
        s["performance"] = supplier_performance(conn, biz, s["id"])
    return sups


def get_supplier(conn, biz: int, sid: int) -> dict:
    s = db.get_owned(conn, "suppliers", sid, biz)
    if not s:
        raise not_found("Supplier")
    s["products"] = db.all_(conn, """SELECT sp.*, p.name AS product, p.unit FROM supplier_products sp
                                     JOIN products p ON p.id = sp.product_id WHERE sp.supplier_id=? AND sp.business_id=?""",
                            (sid, biz))
    s["performance"] = supplier_performance(conn, biz, sid)
    s["purchase_history"] = db.all_(conn, """SELECT id, po_number, status, total, created_at, expected_date, received_at
                                             FROM purchase_orders WHERE business_id=? AND supplier_id=?
                                             ORDER BY id DESC LIMIT 20""", (biz, sid))
    return s


def find_supplier(conn, biz: int, ref) -> dict:
    if isinstance(ref, (int, float)) or (isinstance(ref, str) and ref.isdigit()):
        return get_supplier(conn, biz, int(ref))
    r = db.one(conn, "SELECT id FROM suppliers WHERE business_id=? AND lower(name) LIKE ?", (biz, f"%{str(ref).lower()}%"))
    if not r:
        raise not_found(f"Supplier '{ref}'")
    return get_supplier(conn, biz, r["id"])


def supplier_performance(conn, biz: int, sid: int) -> dict:
    rows = db.all_(conn, """SELECT expected_date, received_at, total FROM purchase_orders
                            WHERE business_id=? AND supplier_id=? AND status='COMPLETED'""", (biz, sid))
    on_time = sum(1 for r in rows if r["expected_date"] and r["received_at"] and r["received_at"][:10] <= r["expected_date"])
    spend = money(sum(r["total"] for r in rows))
    return {"completed_orders": len(rows), "on_time_orders": on_time,
            "on_time_rate_pct": round(on_time / len(rows) * 100, 1) if rows else None,
            "total_spend": spend}


def _round_to_moq(need: float, moq: float, max_stock: float, current: float) -> float:
    moq = max(1.0, moq)
    up = math.ceil(need / moq) * moq
    if current + up <= max_stock * 1.1 or up == moq:
        return up
    return max(moq, math.floor(need / moq) * moq)


def compare_quotes(conn, biz: int, pid: int, quantity: float | None = None) -> dict:
    """Deterministic supplier comparison: prefer the cheapest supplier that can deliver before
    the projected stockout; if none can, pick the fastest. Reliability breaks ties."""
    p = inventory.get_product(conn, biz, pid)
    f = inventory.forecast(conn, biz, pid, product=p)
    need = quantity if quantity else max(0.0, p["max_stock"] - p["current_stock"])
    quotes = db.all_(conn, """SELECT sp.*, s.name AS supplier, s.reliability, s.is_preferred, s.payment_terms
                              FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
                              WHERE sp.business_id=? AND sp.product_id=?""", (biz, pid))
    if not quotes:
        return {"product_id": pid, "product": p["name"], "quotes": [], "recommended": None,
                "rationale": "No supplier offers this product yet."}
    cover = f["days_of_cover"]
    out = []
    for q in quotes:
        qty = _round_to_moq(need, q["min_order_qty"], p["max_stock"], p["current_stock"]) if need > 0 else q["min_order_qty"]
        eta = today() + timedelta(days=q["lead_time_days"])
        arrives_in_time = cover is None or q["lead_time_days"] < cover
        out.append({
            "supplier_id": q["supplier_id"], "supplier": q["supplier"], "unit_price": q["unit_price"],
            "previous_unit_price": q["previous_unit_price"],
            "price_change_pct": round((q["unit_price"] - q["previous_unit_price"]) / q["previous_unit_price"] * 100, 1)
            if q["previous_unit_price"] else None,
            "min_order_qty": q["min_order_qty"], "lead_time_days": q["lead_time_days"], "eta": iso(eta),
            "order_quantity": qty, "total_cost": money(qty * q["unit_price"]),
            "reliability": q["reliability"], "is_preferred": bool(q["is_preferred"]),
            "payment_terms": q["payment_terms"], "arrives_before_stockout": arrives_in_time,
        })
    in_time = [q for q in out if q["arrives_before_stockout"]]
    if in_time:
        best = min(in_time, key=lambda q: (q["unit_price"], -q["reliability"], q["lead_time_days"]))
        cheapest = min(out, key=lambda q: q["unit_price"])
        if cheapest["supplier_id"] != best["supplier_id"]:
            rationale = (f"{cheapest['supplier']} is cheapest at {cheapest['unit_price']:.2f}/{p['unit']} but needs "
                         f"{cheapest['lead_time_days']} days, after the projected stockout in {cover} days. "
                         f"{best['supplier']} delivers in {best['lead_time_days']} days at {best['unit_price']:.2f}/{p['unit']}.")
        else:
            rationale = (f"{best['supplier']} is the lowest-priced supplier that delivers before the projected stockout"
                         + (f" ({cover} days of cover)." if cover is not None else "."))
    else:
        best = min(out, key=lambda q: (q["lead_time_days"], q["unit_price"]))
        rationale = (f"No supplier can deliver before the projected stockout in {cover} days; "
                     f"{best['supplier']} is the fastest option ({best['lead_time_days']} days).")
    for q in out:
        q["recommended"] = q["supplier_id"] == best["supplier_id"]
    out.sort(key=lambda q: (not q["recommended"], q["unit_price"]))
    return {"product_id": pid, "product": p["name"], "unit": p["unit"], "current_stock": p["current_stock"],
            "max_stock": p["max_stock"], "days_of_cover": cover, "quantity_needed": round(need, 1),
            "quotes": out, "recommended": best, "rationale": rationale}


def restock_plan(conn, biz: int, product_ids: list[int] | None = None) -> dict:
    """For every product below the low-stock threshold, forecast + compare suppliers + size the order."""
    products = inventory.low_stock_products(conn, biz)
    if product_ids:
        products = [p for p in products if p["id"] in product_ids]
    open_items = {r["product_id"]: r["po_number"] for r in db.all_(conn, f"""
        SELECT i.product_id, po.po_number FROM purchase_order_items i JOIN purchase_orders po ON po.id = i.po_id
        WHERE po.business_id=? AND po.status IN ({','.join('?' * len(OPEN_PO_STATUSES))})""",
        (biz, *OPEN_PO_STATUSES))}
    lines, total = [], 0.0
    for p in products:
        cmp = compare_quotes(conn, biz, p["id"])
        rec = cmp["recommended"]
        line = {"product_id": p["id"], "product": p["name"], "unit": p["unit"], "stock_pct": p["stock_pct"],
                "level": p["level"], "current_stock": p["current_stock"], "max_stock": p["max_stock"],
                "days_of_cover": p.get("days_of_cover"), "stockout_risk": p.get("stockout_risk"),
                "already_on_order": open_items.get(p["id"])}
        if rec:
            line.update({"supplier_id": rec["supplier_id"], "supplier": rec["supplier"],
                         "quantity": rec["order_quantity"], "unit_price": rec["unit_price"],
                         "line_total": rec["total_cost"], "eta": rec["eta"], "rationale": cmp["rationale"]})
            if not line["already_on_order"]:
                total += rec["total_cost"]
        lines.append(line)
    return {"items": lines, "estimated_total": money(total),
            "note": "Quantities refill each product to its maximum capacity, rounded to supplier minimum order quantities."}


def _next_po_number(conn, biz: int) -> str:
    n = db.scalar(conn, "SELECT COUNT(*) FROM purchase_orders WHERE business_id=?", (biz,)) or 0
    while True:
        n += 1
        num = f"PO-{today().strftime('%y%m')}-{n:04d}"
        if not db.one(conn, "SELECT 1 FROM purchase_orders WHERE business_id=? AND po_number=?", (biz, num)):
            return num


def create_po(conn, biz: int, supplier_id: int, items: list[dict], actor: str, origin: str = "manual",
              notes: str | None = None, request_approval: bool = True) -> dict:
    sup = db.get_owned(conn, "suppliers", supplier_id, biz)
    if not sup:
        raise bad_request("Unknown supplier")
    if not items:
        raise bad_request("A purchase order needs at least one item")
    priced, lead = [], 0
    for it in items:
        qty = float(it.get("quantity") or 0)
        if qty <= 0:
            raise bad_request("Quantities must be positive")
        sp = db.one(conn, """SELECT sp.*, p.name AS product FROM supplier_products sp JOIN products p ON p.id=sp.product_id
                             WHERE sp.business_id=? AND sp.supplier_id=? AND sp.product_id=?""",
                    (biz, supplier_id, it["product_id"]))
        if not sp:
            raise bad_request(f"{sup['name']} does not supply product #{it['product_id']}")
        if qty < sp["min_order_qty"]:
            raise bad_request(f"{sp['product']}: minimum order quantity at {sup['name']} is {sp['min_order_qty']:g}")
        priced.append({"product_id": it["product_id"], "quantity": qty, "unit_price": sp["unit_price"],
                       "line_total": money(qty * sp["unit_price"])})
        lead = max(lead, sp["lead_time_days"])
    total = money(sum(i["line_total"] for i in priced))
    with db.transaction(conn):
        po_id = db.insert(conn, "purchase_orders", {
            "business_id": biz, "po_number": _next_po_number(conn, biz), "supplier_id": supplier_id,
            "status": "DRAFT", "total": total, "created_by": actor, "origin": origin, "notes": notes,
            "expected_date": iso(today() + timedelta(days=lead))})
        for i in priced:
            db.insert(conn, "purchase_order_items", {"po_id": po_id, **i})
        audit(conn, biz, actor_type_for(actor), actor, "purchase_order.create", "purchase_order", po_id,
              {"total": total, "supplier": sup["name"], "origin": origin})
        if request_approval:
            submit_for_approval(conn, biz, po_id, actor)
    return get_po(conn, biz, po_id)


def submit_for_approval(conn, biz: int, po_id: int, actor: str) -> dict:
    from . import approvals
    po = get_po(conn, biz, po_id)
    if po["status"] != "DRAFT":
        raise bad_request(f"Only draft orders can be sent for approval (status is {po['status']})")
    s = settings(conn, biz)
    db.update(conn, "purchase_orders", po_id, {"status": "AWAITING_APPROVAL"}, biz)
    required = "MANAGER" if po["total"] <= s["manager_spending_limit"] else "OWNER"
    # Auto-approval only if the owner explicitly enabled it AND the order is within the configured limit.
    if (s["auto_po_enabled"] and po["origin"] == "automation" and s["auto_approve_limit"] > 0
            and po["total"] <= s["auto_approve_limit"]):
        approve_and_submit(conn, biz, po_id, "automation:auto-approval-policy", "SYSTEM")
        return get_po(conn, biz, po_id)
    approvals.create(conn, biz, "PURCHASE_ORDER", po_id,
                     f"{po['po_number']} · {po['supplier']} · {po['total']:.2f}",
                     ", ".join(f"{i['quantity']:g} {i['unit']} {i['product']}" for i in po["items"]),
                     po["total"], actor, required)
    return get_po(conn, biz, po_id)


def get_po(conn, biz: int, po_id: int) -> dict:
    po = db.one(conn, """SELECT po.*, s.name AS supplier, s.payment_terms FROM purchase_orders po
                         JOIN suppliers s ON s.id = po.supplier_id WHERE po.id=? AND po.business_id=?""", (po_id, biz))
    if not po:
        raise not_found("Purchase order")
    po["items"] = db.all_(conn, """SELECT i.*, p.name AS product, p.unit FROM purchase_order_items i
                                   JOIN products p ON p.id = i.product_id WHERE i.po_id=?""", (po_id,))
    po["approval"] = db.one(conn, """SELECT id, status, required_role FROM approval_requests WHERE business_id=?
                                     AND kind='PURCHASE_ORDER' AND ref_id=? ORDER BY id DESC LIMIT 1""", (biz, po_id))
    return po


def list_pos(conn, biz: int, status: str | None = None) -> list[dict]:
    sql = """SELECT po.*, s.name AS supplier FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
             WHERE po.business_id=?"""
    params: list = [biz]
    if status:
        sql += " AND po.status=?"
        params.append(status)
    pos = db.all_(conn, sql + " ORDER BY po.id DESC", params)
    for po in pos:
        po["items"] = db.all_(conn, """SELECT i.*, p.name AS product, p.unit FROM purchase_order_items i
                                       JOIN products p ON p.id=i.product_id WHERE i.po_id=?""", (po["id"],))
    return pos


def approve_and_submit(conn, biz: int, po_id: int, actor: str, role: str) -> dict:
    """Approval is enforced here, not in the AI layer. Submission to the supplier is simulated."""
    po = get_po(conn, biz, po_id)
    if po["status"] not in ("AWAITING_APPROVAL", "DRAFT"):
        raise bad_request(f"Order is {po['status']}, it cannot be approved")
    s = settings(conn, biz)
    if role not in ("OWNER", "MANAGER", "SYSTEM"):
        raise forbidden("Only an owner or manager can approve purchase orders")
    if role == "MANAGER" and po["total"] > s["manager_spending_limit"]:
        raise forbidden(f"Orders above {s['manager_spending_limit']:.2f} require owner approval")
    t = now_iso()
    with db.transaction(conn):
        db.update(conn, "purchase_orders", po_id, {"status": "APPROVED", "approved_at": t, "approved_by": actor}, biz)
        audit(conn, biz, actor_type_for(actor), actor, "purchase_order.approve", "purchase_order", po_id,
              {"total": po["total"]})
        db.update(conn, "purchase_orders", po_id, {"status": "SUBMITTED", "submitted_at": t}, biz)
        audit(conn, biz, "SYSTEM", "system", "purchase_order.submit", "purchase_order", po_id,
              {"supplier": po["supplier"], "simulated": True,
               "note": "Supplier submission is simulated in this MVP; no real order or payment was sent."})
    return get_po(conn, biz, po_id)


def cancel_po(conn, biz: int, po_id: int, actor: str, reason: str = "") -> dict:
    po = get_po(conn, biz, po_id)
    if po["status"] in ("COMPLETED", "CANCELLED", "PARTIALLY_RECEIVED"):
        raise bad_request(f"Order is {po['status']} and cannot be cancelled")
    db.update(conn, "purchase_orders", po_id, {"status": "CANCELLED"}, biz)
    conn.execute("""UPDATE approval_requests SET status='CANCELLED' WHERE business_id=? AND kind='PURCHASE_ORDER'
                    AND ref_id=? AND status='PENDING'""", (biz, po_id))
    audit(conn, biz, actor_type_for(actor), actor, "purchase_order.cancel", "purchase_order", po_id, {"reason": reason})
    return get_po(conn, biz, po_id)


def _terms_days(terms: str | None) -> int:
    m = re.search(r"(\d+)", terms or "")
    return int(m.group(1)) if m else 30


def receive_po(conn, biz: int, po_id: int, actor: str, quantities: dict[int, float] | None = None) -> dict:
    po = get_po(conn, biz, po_id)
    if po["status"] not in ("SUBMITTED", "PARTIALLY_RECEIVED", "APPROVED"):
        raise bad_request(f"Order is {po['status']}; only submitted orders can be received")
    with db.transaction(conn):
        received_value = 0.0
        for it in po["items"]:
            remaining = it["quantity"] - it["received_qty"]
            qty = remaining if quantities is None else float(quantities.get(it["id"], 0))
            if qty < 0 or qty > remaining + 1e-9:
                raise bad_request(f"Invalid received quantity for {it['product']}")
            if qty == 0:
                continue
            conn.execute("UPDATE purchase_order_items SET received_qty = received_qty + ? WHERE id=?", (qty, it["id"]))
            inventory.adjust_stock(conn, biz, it["product_id"], qty, "restock", f"Received on {po['po_number']}",
                                   actor, ref=po["po_number"])
            received_value += qty * it["unit_price"]
        items = db.all_(conn, "SELECT quantity, received_qty FROM purchase_order_items WHERE po_id=?", (po_id,))
        complete = all(i["received_qty"] >= i["quantity"] - 1e-9 for i in items)
        db.update(conn, "purchase_orders", po_id, {"status": "COMPLETED" if complete else "PARTIALLY_RECEIVED",
                                                    "received_at": now_iso()}, biz)
        if received_value > 0:
            db.insert(conn, "invoices", {"business_id": biz, "kind": "PAYABLE", "number": f"BILL-{po['po_number']}",
                                         "counterparty": po["supplier"], "amount": money(received_value),
                                         "issue_date": iso(today()),
                                         "due_date": iso(today() + timedelta(days=_terms_days(po["payment_terms"]))),
                                         "ref": po["po_number"]})
        audit(conn, biz, actor_type_for(actor), actor, "purchase_order.receive", "purchase_order", po_id,
              {"value": money(received_value), "complete": complete})
    return get_po(conn, biz, po_id)


def draft_restock_orders(conn, biz: int, actor: str, product_ids: list[int] | None = None,
                         origin: str = "agent") -> dict:
    """Create one PO per recommended supplier for low-stock products not already on order.
    Orders are created as AWAITING_APPROVAL — never submitted without approval."""
    plan = restock_plan(conn, biz, product_ids)
    by_supplier: dict[int, list[dict]] = {}
    skipped = []
    for line in plan["items"]:
        if line.get("already_on_order"):
            skipped.append({"product": line["product"], "reason": f"already on order ({line['already_on_order']})"})
            continue
        if not line.get("supplier_id"):
            skipped.append({"product": line["product"], "reason": "no supplier offers this product"})
            continue
        by_supplier.setdefault(line["supplier_id"], []).append(line)
    created = []
    for sid, lines in by_supplier.items():
        note = "Auto-drafted restock: " + "; ".join(l["rationale"] for l in lines if l.get("rationale"))
        po = create_po(conn, biz, sid, [{"product_id": l["product_id"], "quantity": l["quantity"]} for l in lines],
                       actor, origin=origin, notes=note[:900])
        created.append({"po_id": po["id"], "po_number": po["po_number"], "supplier": po["supplier"],
                        "total": po["total"], "status": po["status"], "approval_id": (po.get("approval") or {}).get("id"),
                        "items": [f"{i['quantity']:g} {i['unit']} {i['product']}" for i in po["items"]]})
    return {"created": created, "skipped": skipped,
            "total": money(sum(c["total"] for c in created)),
            "requires_approval": any(c["status"] == "AWAITING_APPROVAL" for c in created)}


def price_changes(conn, biz: int) -> list[dict]:
    return db.all_(conn, """SELECT sp.supplier_id, s.name AS supplier, p.name AS product, sp.unit_price,
                                   sp.previous_unit_price,
                                   ROUND((sp.unit_price - sp.previous_unit_price) * 100.0 / sp.previous_unit_price, 1) AS change_pct
                            FROM supplier_products sp JOIN suppliers s ON s.id=sp.supplier_id
                            JOIN products p ON p.id=sp.product_id
                            WHERE sp.business_id=? AND sp.previous_unit_price IS NOT NULL
                              AND sp.unit_price <> sp.previous_unit_price
                            ORDER BY change_pct DESC""", (biz,))


def expected_eta(po: dict):
    return parse_date(po.get("expected_date"))
