"""Inventory: stock thresholds, deterministic demand forecasting, stock movements and alerts."""
from __future__ import annotations

import math
from datetime import timedelta

from .. import db
from ..web import bad_request, not_found
from .common import audit, actor_type_for, iso, money, settings, today

LEVEL_ORDER = {"HEALTHY": 0, "LOW": 1, "URGENT": 2, "CRITICAL": 3, "OUT": 4}
LEVEL_LABEL = {"HEALTHY": "Healthy", "LOW": "Low Stock", "URGENT": "Urgent", "CRITICAL": "Critical",
               "OUT": "Out of Stock"}


def stock_pct(current: float, max_stock: float) -> float:
    """Stock Percentage = (Current Stock / Maximum Stock Capacity) x 100"""
    if not max_stock:
        return 0.0
    return round(current / max_stock * 100, 1)


def level_for(current: float, max_stock: float, s: dict) -> str:
    if current <= 0:
        return "OUT"
    pct = stock_pct(current, max_stock)
    if pct < s["critical_stock_pct"]:
        return "CRITICAL"
    if pct < s["urgent_stock_pct"]:
        return "URGENT"
    if pct < s["low_stock_pct"]:
        return "LOW"
    return "HEALTHY"


def _decorate(conn, p: dict, s: dict) -> dict:
    p["stock_pct"] = stock_pct(p["current_stock"], p["max_stock"])
    p["level"] = level_for(p["current_stock"], p["max_stock"], s)
    p["status_label"] = LEVEL_LABEL[p["level"]]
    p["reorder_threshold_qty"] = round(p["max_stock"] * s["low_stock_pct"] / 100, 1)
    p["stock_value"] = money(p["current_stock"] * p["unit_cost"])
    return p


def list_products(conn, biz: int, include_forecast: bool = True) -> list[dict]:
    s = settings(conn, biz)
    rows = db.all_(conn, """SELECT p.*, sp.name AS supplier_name FROM products p
                            LEFT JOIN suppliers sp ON sp.id = p.preferred_supplier_id
                            WHERE p.business_id = ? AND p.active = 1 ORDER BY p.name""", (biz,))
    out = []
    for p in rows:
        _decorate(conn, p, s)
        if include_forecast:
            f = forecast(conn, biz, p["id"], product=p, s=s)
            p["avg_daily_sales"] = f["avg_daily_sales"]
            p["days_of_cover"] = f["days_of_cover"]
            p["depletion_date"] = f["depletion_date"]
            p["stockout_risk"] = f["stockout_risk"]
        out.append(p)
    return out


def get_product(conn, biz: int, pid: int) -> dict:
    p = db.one(conn, """SELECT p.*, sp.name AS supplier_name FROM products p
                        LEFT JOIN suppliers sp ON sp.id = p.preferred_supplier_id
                        WHERE p.id = ? AND p.business_id = ?""", (pid, biz))
    if not p:
        raise not_found("Product")
    return _decorate(conn, p, settings(conn, biz))


def find_product(conn, biz: int, ref) -> dict:
    """Resolve a product by id, SKU or (case-insensitive) name fragment — used by AI tools."""
    if isinstance(ref, (int, float)) or (isinstance(ref, str) and ref.isdigit()):
        return get_product(conn, biz, int(ref))
    r = db.one(conn, "SELECT id FROM products WHERE business_id=? AND (lower(sku)=lower(?) OR lower(name)=lower(?))",
               (biz, ref, ref))
    if not r:
        r = db.one(conn, "SELECT id FROM products WHERE business_id=? AND lower(name) LIKE ? ORDER BY length(name)",
                   (biz, f"%{str(ref).lower()}%"))
    if not r:
        raise not_found(f"Product '{ref}'")
    return get_product(conn, biz, r["id"])


def low_stock_products(conn, biz: int) -> list[dict]:
    return [p for p in list_products(conn, biz) if p["level"] != "HEALTHY"]


def _sales_between(conn, biz: int, pid: int, start, end) -> float:
    v = db.scalar(conn, """SELECT COALESCE(SUM(-quantity),0) FROM inventory_movements
                           WHERE business_id=? AND product_id=? AND kind='sale'
                           AND occurred_on > ? AND occurred_on <= ?""", (biz, pid, iso(start), iso(end)))
    return float(v or 0)


def best_lead_time(conn, biz: int, pid: int, preferred_supplier_id: int | None) -> int | None:
    if preferred_supplier_id:
        r = db.one(conn, "SELECT lead_time_days FROM supplier_products WHERE business_id=? AND product_id=? AND supplier_id=?",
                   (biz, pid, preferred_supplier_id))
        if r:
            return r["lead_time_days"]
    r = db.one(conn, "SELECT MIN(lead_time_days) AS l FROM supplier_products WHERE business_id=? AND product_id=?",
               (biz, pid))
    return r["l"] if r and r["l"] is not None else None


def forecast(conn, biz: int, pid: int, product: dict | None = None, s: dict | None = None) -> dict:
    """Transparent moving-average forecast. No ML, no LLM: every number is reproducible."""
    s = s or settings(conn, biz)
    p = product or get_product(conn, biz, pid)
    window = int(s["forecast_window_days"])
    t = today()
    recent = _sales_between(conn, biz, pid, t - timedelta(days=window), t)
    prior = _sales_between(conn, biz, pid, t - timedelta(days=2 * window), t - timedelta(days=window))
    avg = recent / window
    prior_avg = prior / window
    trend_pct = round((avg - prior_avg) / prior_avg * 100, 1) if prior_avg > 0 else None
    cover = round(p["current_stock"] / avg, 1) if avg > 0 else None
    depletion = iso(t + timedelta(days=math.floor(cover))) if cover is not None else None
    lead = best_lead_time(conn, biz, pid, p.get("preferred_supplier_id"))
    safety = int(s["safety_stock_days"])
    if cover is None:
        risk = "NONE"
    elif lead is not None and cover <= lead:
        risk = "HIGH"
    elif cover <= (lead or 0) + safety + 2:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    need = max(0.0, p["max_stock"] - p["current_stock"])
    return {
        "product_id": pid,
        "product": p["name"],
        "unit": p["unit"],
        "method": f"{window}-day moving average of recorded sales; refill to max capacity",
        "window_days": window,
        "units_sold_in_window": round(recent, 1),
        "avg_daily_sales": round(avg, 2),
        "weekly_demand": round(avg * 7, 1),
        "previous_window_avg_daily_sales": round(prior_avg, 2),
        "demand_trend_pct": trend_pct,
        "current_stock": p["current_stock"],
        "max_stock": p["max_stock"],
        "stock_pct": stock_pct(p["current_stock"], p["max_stock"]),
        "days_of_cover": cover,
        "depletion_date": depletion,
        "supplier_lead_time_days": lead,
        "stockout_risk": risk,
        "quantity_to_refill": round(need, 1),
    }


def daily_sales_series(conn, biz: int, pid: int, days: int = 30) -> list[dict]:
    t = today()
    rows = db.all_(conn, """SELECT occurred_on AS d, SUM(-quantity) AS q FROM inventory_movements
                            WHERE business_id=? AND product_id=? AND kind='sale' AND occurred_on > ?
                            GROUP BY occurred_on""", (biz, pid, iso(t - timedelta(days=days))))
    by = {r["d"]: r["q"] for r in rows}
    return [{"date": iso(t - timedelta(days=i)), "units": round(by.get(iso(t - timedelta(days=i)), 0), 1)}
            for i in range(days - 1, -1, -1)]


def movements(conn, biz: int, pid: int, limit: int = 50) -> list[dict]:
    get_product(conn, biz, pid)
    return db.all_(conn, """SELECT * FROM inventory_movements WHERE business_id=? AND product_id=?
                            ORDER BY occurred_on DESC, id DESC LIMIT ?""", (biz, pid, limit))


def adjust_stock(conn, biz: int, pid: int, delta: float, kind: str, note: str, actor: str,
                 ref: str | None = None) -> dict:
    if kind not in ("sale", "restock", "adjustment", "waste"):
        raise bad_request("Invalid movement kind")
    p = get_product(conn, biz, pid)
    new = p["current_stock"] + delta
    if new < 0:
        raise bad_request(f"Adjustment would make stock negative ({p['current_stock']} {p['unit']} on hand)")
    with db.transaction(conn):
        db.insert(conn, "inventory_movements", {"business_id": biz, "product_id": pid, "kind": kind,
                                                "quantity": delta, "note": note, "ref": ref,
                                                "occurred_on": iso(today())})
        db.update(conn, "products", pid, {"current_stock": new}, biz)
        audit(conn, biz, actor_type_for(actor), actor, f"inventory.{kind}", "product", pid,
              {"delta": delta, "from": p["current_stock"], "to": new, "note": note})
        sync_alerts(conn, biz)
    return get_product(conn, biz, pid)


def sync_alerts(conn, biz: int) -> list[dict]:
    """Open/escalate/resolve stock alerts from current levels. Returns newly opened or escalated alerts."""
    s = settings(conn, biz)
    changed = []
    for p in db.all_(conn, "SELECT * FROM products WHERE business_id=? AND active=1", (biz,)):
        lvl = level_for(p["current_stock"], p["max_stock"], s)
        pct = stock_pct(p["current_stock"], p["max_stock"])
        open_ = db.one(conn, "SELECT * FROM stock_alerts WHERE business_id=? AND product_id=? AND status='OPEN'",
                       (biz, p["id"]))
        if lvl == "HEALTHY":
            if open_:
                db.update(conn, "stock_alerts", open_["id"], {"status": "RESOLVED",
                          "resolved_at": iso(today())}, biz)
            continue
        if not open_:
            aid = db.insert(conn, "stock_alerts", {"business_id": biz, "product_id": p["id"], "level": lvl,
                                                   "stock_pct": pct})
            changed.append({"alert_id": aid, "product_id": p["id"], "product": p["name"], "level": lvl,
                            "stock_pct": pct, "new": True})
        elif LEVEL_ORDER[lvl] > LEVEL_ORDER[open_["level"]]:
            db.update(conn, "stock_alerts", open_["id"], {"level": lvl, "stock_pct": pct}, biz)
            changed.append({"alert_id": open_["id"], "product_id": p["id"], "product": p["name"], "level": lvl,
                            "stock_pct": pct, "escalated": True})
        else:
            db.update(conn, "stock_alerts", open_["id"], {"stock_pct": pct, "level": lvl}, biz)
    return changed


def open_alerts(conn, biz: int) -> list[dict]:
    return db.all_(conn, """SELECT a.*, p.name AS product, p.current_stock, p.max_stock, p.unit
                            FROM stock_alerts a JOIN products p ON p.id = a.product_id
                            WHERE a.business_id=? AND a.status='OPEN'
                            ORDER BY a.stock_pct ASC""", (biz,))


def create_product(conn, biz: int, data: dict, actor: str) -> dict:
    if data.get("preferred_supplier_id") and not db.get_owned(conn, "suppliers", data["preferred_supplier_id"], biz):
        raise bad_request("Unknown supplier")
    if db.one(conn, "SELECT 1 FROM products WHERE business_id=? AND sku=?", (biz, data["sku"])):
        raise bad_request("SKU already exists")
    pid = db.insert(conn, "products", {"business_id": biz, **data})
    audit(conn, biz, actor_type_for(actor), actor, "product.create", "product", pid, data)
    sync_alerts(conn, biz)
    return get_product(conn, biz, pid)


def update_product(conn, biz: int, pid: int, data: dict, actor: str) -> dict:
    get_product(conn, biz, pid)
    if data.get("preferred_supplier_id") and not db.get_owned(conn, "suppliers", data["preferred_supplier_id"], biz):
        raise bad_request("Unknown supplier")
    if "current_stock" in data:
        raise bad_request("Use the stock adjustment endpoint to change stock levels")
    db.update(conn, "products", pid, data, biz)
    audit(conn, biz, actor_type_for(actor), actor, "product.update", "product", pid, data)
    sync_alerts(conn, biz)
    return get_product(conn, biz, pid)


def delete_product(conn, biz: int, pid: int, actor: str) -> None:
    get_product(conn, biz, pid)
    db.update(conn, "products", pid, {"active": 0}, biz)  # soft delete keeps PO / movement history intact
    conn.execute("UPDATE stock_alerts SET status='RESOLVED' WHERE business_id=? AND product_id=?", (biz, pid))
    audit(conn, biz, actor_type_for(actor), actor, "product.archive", "product", pid)


def inventory_value(conn, biz: int) -> float:
    return money(db.scalar(conn, "SELECT COALESCE(SUM(current_stock*unit_cost),0) FROM products WHERE business_id=? AND active=1",
                           (biz,)))
