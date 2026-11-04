"""REST API routes. All business data access is scoped to the business in the caller's token."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from . import config, db, security
from .ai import features, meta_client, orchestrator
from .seed import reset_demo, seed_demo
from .services import (approvals, automations, contracts, dashboard, finance, insights, inventory, payroll, procurement,
                       reports)
from .services.common import audit, business, iso, month_key, settings, today
from .web import ApiError, Ctx, bad_request, endpoint, forbidden, int_param, not_found, query_int

Role = Literal["OWNER", "MANAGER", "HR", "FINANCE", "INVENTORY", "STAFF"]


# ============================================================ auth
class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    business_name: str = Field(min_length=1, max_length=120)
    business_type: str = Field(default="Retail", max_length=60)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    load_demo_data: bool = False

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("invalid email")
        return v.lower()


class LoginIn(BaseModel):
    email: str
    password: str
    business_id: Optional[int] = None


def _me(conn, ctx_user_id: int, biz: int) -> dict:
    u = db.one(conn, "SELECT id, email, name FROM users WHERE id=?", (ctx_user_id,))
    m = db.one(conn, "SELECT role FROM business_members WHERE user_id=? AND business_id=?", (ctx_user_id, biz))
    b = business(conn, biz)
    memberships = db.all_(conn, """SELECT b.id, b.name, m.role FROM business_members m JOIN businesses b ON b.id=m.business_id
                                   WHERE m.user_id=? ORDER BY b.id""", (ctx_user_id,))
    return {"user": u, "role": m["role"], "permissions": security.perms_for(m["role"]), "business": b,
            "memberships": memberships, "ai": {"mode": config.ai_mode(), "model": config.META_MODEL,
                                               "asr_model": config.META_ASR_MODEL}}


@endpoint(auth=False, body=RegisterIn, limit=10)
def register(request, ctx, body: RegisterIn):
    with db.connect() as conn:
        if db.one(conn, "SELECT 1 FROM users WHERE email=?", (body.email,)):
            raise bad_request("An account with this email already exists")
        with db.transaction(conn):
            uid = db.insert(conn, "users", {"email": body.email, "name": body.name, "password_hash": security.hash_password(body.password)})
            if body.load_demo_data:
                biz = seed_demo(conn, body.business_name, owner_user_id=uid)
            else:
                biz = db.insert(conn, "businesses", {"name": body.business_name, "business_type": body.business_type,
                                                     "currency": body.currency.upper()})
                db.insert(conn, "business_settings", {"business_id": biz})
                db.insert(conn, "business_members", {"business_id": biz, "user_id": uid, "role": "OWNER"})
                db.insert(conn, "financial_accounts", {"business_id": biz, "name": "Operating account", "opening_balance": 0,
                                                       "opening_date": iso(today())})
            audit(conn, biz, "USER", body.name, "auth.register", "user", uid)
        return {"token": security.issue_token(uid, biz), **_me(conn, uid, biz)}


@endpoint(auth=False, body=LoginIn, limit=20)
def login(request, ctx, body: LoginIn):
    with db.connect() as conn:
        u = db.one(conn, "SELECT * FROM users WHERE email=?", (body.email.lower().strip(),))
        if not u or not security.verify_password(body.password, u["password_hash"]):
            raise ApiError(401, "Incorrect email or password", "invalid_credentials")
        ms = db.all_(conn, "SELECT business_id FROM business_members WHERE user_id=? ORDER BY business_id", (u["id"],))
        if not ms:
            raise ApiError(403, "This account is not a member of any business", "no_business")
        biz = body.business_id if body.business_id in [m["business_id"] for m in ms] else ms[0]["business_id"]
        audit(conn, biz, "USER", u["name"], "auth.login", "user", u["id"])
        return {"token": security.issue_token(u["id"], biz), **_me(conn, u["id"], biz)}


@endpoint()
def logout(request, ctx: Ctx, _):
    security.revoke(ctx.token_payload)
    return {"ok": True}


@endpoint()
def me(request, ctx: Ctx, _):
    with db.connect() as conn:
        return _me(conn, ctx.user_id, ctx.business_id)


class SwitchIn(BaseModel):
    business_id: int


@endpoint(body=SwitchIn)
def switch_business(request, ctx: Ctx, body: SwitchIn):
    with db.connect() as conn:
        if not db.one(conn, "SELECT 1 FROM business_members WHERE user_id=? AND business_id=?", (ctx.user_id, body.business_id)):
            raise forbidden("You are not a member of that business")
        return {"token": security.issue_token(ctx.user_id, body.business_id), **_me(conn, ctx.user_id, body.business_id)}


# ============================================================ business & settings
@endpoint()
def get_business(request, ctx: Ctx, _):
    with db.connect() as conn:
        members = db.all_(conn, """SELECT u.name, u.email, m.role FROM business_members m JOIN users u ON u.id=m.user_id
                                   WHERE m.business_id=? ORDER BY m.role""", (ctx.business_id,)) if ctx.can("audit.read") else []
        return {"business": business(conn, ctx.business_id), "settings": settings(conn, ctx.business_id), "members": members,
                "ai": {"mode": config.ai_mode(), "model": config.META_MODEL, "asr_model": config.META_ASR_MODEL,
                       "base_url": config.META_BASE_URL, "key_configured": bool(config.META_API_KEY)}}


class SettingsIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    business_type: Optional[str] = Field(default=None, max_length=60)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    jurisdiction: Optional[str] = Field(default=None, max_length=20)
    low_stock_pct: Optional[float] = Field(default=None, gt=0, lt=100)
    urgent_stock_pct: Optional[float] = Field(default=None, gt=0, lt=100)
    critical_stock_pct: Optional[float] = Field(default=None, gt=0, lt=100)
    auto_po_enabled: Optional[bool] = None
    auto_approve_limit: Optional[float] = Field(default=None, ge=0)
    manager_spending_limit: Optional[float] = Field(default=None, ge=0)
    payroll_day: Optional[int] = Field(default=None, ge=1, le=28)
    contract_reminder_days: Optional[str] = Field(default=None, pattern=r"^\d{1,3}(,\d{1,3})*$")
    invoice_overdue_days: Optional[int] = Field(default=None, ge=1, le=365)
    forecast_window_days: Optional[int] = Field(default=None, ge=7, le=90)
    safety_stock_days: Optional[int] = Field(default=None, ge=0, le=30)
    budgets: Optional[dict[str, float]] = None


@endpoint(perm="settings.write", body=SettingsIn)
def put_settings(request, ctx: Ctx, body: SettingsIn):
    data = body.model_dump(exclude_none=True)
    b_fields = {k: data.pop(k) for k in ("name", "business_type", "currency", "jurisdiction") if k in data}
    if "currency" in b_fields:
        b_fields["currency"] = b_fields["currency"].upper()
    with db.connect() as conn:
        s = settings(conn, ctx.business_id)
        crit, urg, low = (data.get("critical_stock_pct", s["critical_stock_pct"]), data.get("urgent_stock_pct", s["urgent_stock_pct"]),
                          data.get("low_stock_pct", s["low_stock_pct"]))
        if not crit < urg < low:
            raise bad_request("Thresholds must satisfy critical < urgent < low")
        if "auto_po_enabled" in data:
            data["auto_po_enabled"] = int(data["auto_po_enabled"])
        if "budgets" in data:
            data["budgets"] = json.dumps(data["budgets"])
        if data:
            sets = ", ".join(f"{k}=?" for k in data)
            conn.execute(f"UPDATE business_settings SET {sets} WHERE business_id=?", (*data.values(), ctx.business_id))
        if b_fields:
            db.update(conn, "businesses", ctx.business_id, b_fields)
        audit(conn, ctx.business_id, "USER", ctx.actor, "settings.update", "business", ctx.business_id, {**data, **b_fields})
        inventory.sync_alerts(conn, ctx.business_id)
        return {"business": business(conn, ctx.business_id), "settings": settings(conn, ctx.business_id)}


@endpoint(perm="settings.write", limit=5)
def reset_demo_route(request, ctx: Ctx, _):
    with db.connect() as conn:
        if not business(conn, ctx.business_id)["is_demo"]:
            raise bad_request("Only demo businesses can be reset")
        with db.transaction(conn):
            new_biz = reset_demo(conn, ctx.business_id)
        if not db.one(conn, "SELECT 1 FROM business_members WHERE user_id=? AND business_id=?", (ctx.user_id, new_biz)):
            db.insert(conn, "business_members", {"business_id": new_biz, "user_id": ctx.user_id, "role": "OWNER"})
        return {"token": security.issue_token(ctx.user_id, new_biz), **_me(conn, ctx.user_id, new_biz)}


# ============================================================ dashboard
@endpoint(perm="dashboard.read")
def dash_overview(request, ctx: Ctx, _):
    with db.connect() as conn:
        return dashboard.overview(conn, ctx.business_id, ctx.can("finance.read"), ctx.can("salary.read"))


@endpoint(perm="dashboard.read")
def dash_alerts(request, ctx: Ctx, _):
    with db.connect() as conn:
        return dashboard.alerts(conn, ctx.business_id, ctx.can("finance.read"), ctx.can("salary.read"))


@endpoint(perm="finance.read")
def dash_briefing(request, ctx: Ctx, _):
    force = request.query_params.get("refresh") == "1"
    if force:
        from .web import rate_limit
        rate_limit(f"u{ctx.user_id}:briefing", 6)
    with db.connect() as conn:
        return features.briefing(conn, ctx.business_id, force)


# ============================================================ inventory
class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="General", max_length=60)
    unit: str = Field(default="unit", max_length=40)
    current_stock: float = Field(default=0, ge=0)
    max_stock: float = Field(gt=0)
    unit_cost: float = Field(default=0, ge=0)
    unit_price: float = Field(default=0, ge=0)
    preferred_supplier_id: Optional[int] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=60)
    unit: Optional[str] = Field(default=None, max_length=40)
    max_stock: Optional[float] = Field(default=None, gt=0)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    preferred_supplier_id: Optional[int] = None


class AdjustIn(BaseModel):
    delta: float
    kind: Literal["sale", "restock", "adjustment", "waste"] = "adjustment"
    note: str = Field(default="", max_length=300)

    @field_validator("delta")
    @classmethod
    def _nz(cls, v):
        if v == 0:
            raise ValueError("delta cannot be zero")
        return v


@endpoint(perm="inventory.read")
def inv_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        s = settings(conn, ctx.business_id)
        return {"products": inventory.list_products(conn, ctx.business_id),
                "thresholds": {k: s[k] for k in ("low_stock_pct", "urgent_stock_pct", "critical_stock_pct")},
                "alerts": inventory.open_alerts(conn, ctx.business_id), "inventory_value": inventory.inventory_value(conn, ctx.business_id)}


@endpoint(perm="inventory.write", body=ProductIn)
def inv_create(request, ctx: Ctx, body: ProductIn):
    with db.connect() as conn:
        return inventory.create_product(conn, ctx.business_id, body.model_dump(), ctx.actor)


@endpoint(perm="inventory.write", body=ProductUpdate)
def inv_update(request, ctx: Ctx, body: ProductUpdate):
    with db.connect() as conn:
        return inventory.update_product(conn, ctx.business_id, int_param(request, "id"), body.model_dump(exclude_none=True), ctx.actor)


@endpoint(perm="inventory.write")
def inv_delete(request, ctx: Ctx, _):
    with db.connect() as conn:
        inventory.delete_product(conn, ctx.business_id, int_param(request, "id"), ctx.actor)
        return {"ok": True}


@endpoint(perm="inventory.read")
def inv_low(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"products": inventory.low_stock_products(conn, ctx.business_id)}


@endpoint(perm="inventory.write", body=AdjustIn)
def inv_adjust(request, ctx: Ctx, body: AdjustIn):
    pid = int_param(request, "id")
    with db.connect() as conn:
        p = inventory.adjust_stock(conn, ctx.business_id, pid, body.delta, body.kind, body.note or "Manual adjustment", ctx.actor)
        # Event-driven automation: a stock change may cross a threshold.
        triggered = automations.run_all(conn, ctx.business_id, "event:stock_change", {"STOCK_BELOW_PCT", "STOCK_CRITICAL"}) \
            if p["level"] != "HEALTHY" else []
        return {"product": p, "automations": [t for t in triggered if t["status"] == "TRIGGERED"]}


@endpoint(perm="inventory.read")
def inv_forecast(request, ctx: Ctx, _):
    pid = int_param(request, "id")
    with db.connect() as conn:
        return {"forecast": inventory.forecast(conn, ctx.business_id, pid),
                "daily_sales": inventory.daily_sales_series(conn, ctx.business_id, pid, 30)}


@endpoint(perm="inventory.read")
def inv_history(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"movements": inventory.movements(conn, ctx.business_id, int_param(request, "id"), 100)}


# ============================================================ procurement
class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    payment_terms: str = "Net 30"
    reliability: float = Field(default=4.0, ge=0, le=5)
    is_preferred: bool = False


class SupplierProductIn(BaseModel):
    product_id: int
    unit_price: float = Field(ge=0)
    min_order_qty: float = Field(default=1, gt=0)
    lead_time_days: int = Field(default=3, ge=0, le=120)


class POItem(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class POIn(BaseModel):
    supplier_id: int
    items: list[POItem] = Field(min_length=1, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ReceiveIn(BaseModel):
    quantities: Optional[dict[int, float]] = None


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@endpoint(perm="procurement.read")
def sup_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"suppliers": procurement.list_suppliers(conn, ctx.business_id), "price_changes": procurement.price_changes(conn, ctx.business_id)}


@endpoint(perm="procurement.read")
def sup_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        return procurement.get_supplier(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="procurement.write", body=SupplierIn)
def sup_create(request, ctx: Ctx, body: SupplierIn):
    with db.connect() as conn:
        sid = db.insert(conn, "suppliers", {"business_id": ctx.business_id, **body.model_dump(), "is_preferred": int(body.is_preferred)})
        audit(conn, ctx.business_id, "USER", ctx.actor, "supplier.create", "supplier", sid, body.model_dump())
        return procurement.get_supplier(conn, ctx.business_id, sid)


@endpoint(perm="procurement.write", body=SupplierProductIn)
def sup_add_product(request, ctx: Ctx, body: SupplierProductIn):
    sid = int_param(request, "id")
    with db.connect() as conn:
        procurement.get_supplier(conn, ctx.business_id, sid)
        inventory.get_product(conn, ctx.business_id, body.product_id)
        ex = db.one(conn, "SELECT * FROM supplier_products WHERE supplier_id=? AND product_id=?", (sid, body.product_id))
        if ex:
            db.update(conn, "supplier_products", ex["id"], {**body.model_dump(exclude={"product_id"}),
                                                            "previous_unit_price": ex["unit_price"] if ex["unit_price"] != body.unit_price else ex["previous_unit_price"]},
                      ctx.business_id)
        else:
            db.insert(conn, "supplier_products", {"business_id": ctx.business_id, "supplier_id": sid, **body.model_dump()})
        audit(conn, ctx.business_id, "USER", ctx.actor, "supplier.price", "supplier", sid, body.model_dump())
        return procurement.get_supplier(conn, ctx.business_id, sid)


@endpoint(perm="procurement.read")
def po_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"purchase_orders": procurement.list_pos(conn, ctx.business_id, request.query_params.get("status"))}


@endpoint(perm="procurement.read")
def po_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        return procurement.get_po(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="procurement.write", body=POIn)
def po_create(request, ctx: Ctx, body: POIn):
    with db.connect() as conn:
        return procurement.create_po(conn, ctx.business_id, body.supplier_id, [i.model_dump() for i in body.items], ctx.actor,
                                     "manual", body.notes)


def _approval_for(conn, biz, kind, ref_id):
    a = db.one(conn, "SELECT id FROM approval_requests WHERE business_id=? AND kind=? AND ref_id=? AND status='PENDING'", (biz, kind, ref_id))
    return a["id"] if a else None


@endpoint(perm="procurement.approve")
def po_approve(request, ctx: Ctx, _):
    pid = int_param(request, "id")
    with db.connect() as conn:
        aid = _approval_for(conn, ctx.business_id, "PURCHASE_ORDER", pid)
        if aid:
            return approvals.decide(conn, ctx.business_id, aid, True, ctx.actor, ctx.role)["result"]
        return procurement.approve_and_submit(conn, ctx.business_id, pid, ctx.actor, ctx.role)


@endpoint(perm="procurement.write", body=ReceiveIn)
def po_receive(request, ctx: Ctx, body: ReceiveIn):
    with db.connect() as conn:
        po = procurement.receive_po(conn, ctx.business_id, int_param(request, "id"), ctx.actor, body.quantities)
        insights.refresh(conn, ctx.business_id)
        return po


@endpoint(perm="procurement.write", body=ReasonIn)
def po_cancel(request, ctx: Ctx, body: ReasonIn):
    with db.connect() as conn:
        return procurement.cancel_po(conn, ctx.business_id, int_param(request, "id"), ctx.actor, body.reason)


@endpoint(perm="procurement.read")
def restock_plan(request, ctx: Ctx, _):
    with db.connect() as conn:
        return procurement.restock_plan(conn, ctx.business_id)


class DraftIn(BaseModel):
    product_ids: Optional[list[int]] = None


@endpoint(perm="procurement.write", body=DraftIn)
def draft_restock(request, ctx: Ctx, body: DraftIn):
    with db.connect() as conn:
        return procurement.draft_restock_orders(conn, ctx.business_id, ctx.actor, body.product_ids, origin="manual")


@endpoint(perm="procurement.read")
def quotes(request, ctx: Ctx, _):
    q = request.query_params.get("quantity")
    with db.connect() as conn:
        return procurement.compare_quotes(conn, ctx.business_id, int_param(request, "id"), float(q) if q else None)


# ============================================================ finance
class TxnIn(BaseModel):
    direction: Literal["INCOME", "EXPENSE"]
    amount: float = Field(gt=0, lt=1e9)
    category: Optional[str] = Field(default=None, max_length=60)
    description: str = Field(default="", max_length=300)
    txn_date: date
    payment_method: Optional[str] = Field(default="Bank transfer", max_length=40)
    counterparty: Optional[str] = Field(default=None, max_length=120)
    status: Literal["PAID", "PENDING"] = "PAID"


class TxnUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=60)
    description: Optional[str] = Field(default=None, max_length=300)
    amount: Optional[float] = Field(default=None, gt=0)
    txn_date: Optional[date] = None
    counterparty: Optional[str] = None
    status: Optional[Literal["PAID", "PENDING"]] = None


class InvoiceIn(BaseModel):
    kind: Literal["RECEIVABLE", "PAYABLE"]
    number: str = Field(min_length=1, max_length=40)
    counterparty: str = Field(min_length=1, max_length=120)
    amount: float = Field(gt=0)
    issue_date: date
    due_date: date


@endpoint(perm="finance.read")
def fin_summary(request, ctx: Ctx, _):
    with db.connect() as conn:
        return finance.summary(conn, ctx.business_id, request.query_params.get("period"))


@endpoint(perm="finance.read")
def fin_transactions(request, ctx: Ctx, _):
    q = request.query_params
    with db.connect() as conn:
        return finance.list_transactions(conn, ctx.business_id, q.get("direction"), q.get("category"), q.get("start"), q.get("end"),
                                         q.get("q"), query_int(request, "limit", 50, 1, 200), query_int(request, "offset", 0, 0, 10 ** 6))


@endpoint(perm="finance.write", body=TxnIn)
def fin_create_txn(request, ctx: Ctx, body: TxnIn):
    data = body.model_dump()
    data["txn_date"] = iso(body.txn_date)
    with db.connect() as conn:
        return finance.record_transaction(conn, ctx.business_id, data, ctx.actor)


@endpoint(perm="finance.write", body=TxnUpdate)
def fin_update_txn(request, ctx: Ctx, body: TxnUpdate):
    data = body.model_dump(exclude_none=True)
    if "txn_date" in data:
        data["txn_date"] = iso(data["txn_date"])
    with db.connect() as conn:
        return finance.update_transaction(conn, ctx.business_id, int_param(request, "id"), data, ctx.actor)


@endpoint(perm="finance.read")
def fin_cashflow(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"monthly": finance.monthly_series(conn, ctx.business_id, 6), "cash_trend": finance.cash_trend(conn, ctx.business_id, 60),
                "expense_breakdown": finance.category_breakdown(conn, ctx.business_id, "EXPENSE", today().replace(day=1), today()),
                "expense_breakdown_30d": finance.category_breakdown(conn, ctx.business_id, "EXPENSE",
                                                                    date.fromordinal(today().toordinal() - 29), today()),
                "anomalies": finance.spending_anomalies(conn, ctx.business_id), "budgets": finance.budget_status(conn, ctx.business_id)}


@endpoint(perm="finance.read")
def fin_forecast(request, ctx: Ctx, _):
    with db.connect() as conn:
        return finance.forecast(conn, ctx.business_id, query_int(request, "days", 30, 7, 120))


@endpoint(perm="finance.read")
def fin_invoices(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"receivables": finance.receivables(conn, ctx.business_id), "payables": finance.payables(conn, ctx.business_id),
                "paid": db.all_(conn, "SELECT * FROM invoices WHERE business_id=? AND status='PAID' ORDER BY paid_date DESC LIMIT 20",
                                (ctx.business_id,))}


@endpoint(perm="finance.write", body=InvoiceIn)
def fin_create_invoice(request, ctx: Ctx, body: InvoiceIn):
    if body.due_date < body.issue_date:
        raise bad_request("Due date cannot be before issue date")
    with db.connect() as conn:
        iid = db.insert(conn, "invoices", {"business_id": ctx.business_id, **body.model_dump(), "issue_date": iso(body.issue_date),
                                           "due_date": iso(body.due_date)})
        audit(conn, ctx.business_id, "USER", ctx.actor, "invoice.create", "invoice", iid, {"amount": body.amount, "kind": body.kind})
        return db.get_owned(conn, "invoices", iid, ctx.business_id)


@endpoint(perm="finance.write")
def fin_pay_invoice(request, ctx: Ctx, _):
    with db.connect() as conn:
        return finance.mark_invoice_paid(conn, ctx.business_id, int_param(request, "id"), ctx.actor)


@endpoint(perm="finance.read")
def fin_meta(request, ctx: Ctx, _):
    return {"income_categories": finance.INCOME_CATEGORIES, "expense_categories": finance.EXPENSE_CATEGORIES,
            "definitions": finance.METRIC_DEFINITIONS}


async def _read_upload(request: Request, field: str = "file"):
    form = await request.form(max_files=1, max_fields=20)
    f = form.get(field)
    if f is None or not hasattr(f, "read"):
        raise bad_request(f"Missing file field '{field}'")
    data = await f.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ApiError(413, f"File exceeds {config.MAX_UPLOAD_MB} MB", "too_large")
    return f.filename or "upload", f.content_type, data, form


def _save_upload(biz: int, filename: str, data: bytes) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-80:]
    folder = config.UPLOAD_DIR / str(biz)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:10]}_{safe}"
    path.write_bytes(data)
    return str(path)


@endpoint(perm="finance.write", limit=20)
async def fin_receipt(request: Request, ctx: Ctx, _):
    tid = int_param(request, "id")
    filename, mime, data, _form = await _read_upload(request)
    if not re.search(r"\.(pdf|png|jpe?g|webp|txt)$", filename, re.I):
        raise bad_request("Receipts must be PDF, image or text files")
    from starlette.concurrency import run_in_threadpool

    def save():
        with db.connect() as conn:
            if not db.get_owned(conn, "transactions", tid, ctx.business_id):
                raise not_found("Transaction")
            path = _save_upload(ctx.business_id, filename, data)
            did = contracts.store_document(conn, ctx.business_id, filename, mime, path, [], ctx.actor)
            db.update(conn, "transactions", tid, {"document_id": did}, ctx.business_id)
            audit(conn, ctx.business_id, "USER", ctx.actor, "transaction.receipt", "transaction", tid, {"document_id": did})
            return db.get_owned(conn, "transactions", tid, ctx.business_id)
    return await run_in_threadpool(save)


# ============================================================ employees & payroll
class EmployeeIn(BaseModel):
    emp_code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    job_title: Optional[str] = Field(default=None, max_length=80)
    department: Optional[str] = Field(default=None, max_length=80)
    employment_type: Literal["MONTHLY", "HOURLY"]
    start_date: Optional[date] = None
    base_salary: float = Field(default=0, ge=0)
    hourly_rate: float = Field(default=0, ge=0)
    allowance: float = Field(default=0, ge=0)
    standard_hours: float = Field(default=44, gt=0, le=80)
    leave_entitlement: float = Field(default=14, ge=0)
    payment_details_on_file: bool = True


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    base_salary: Optional[float] = Field(default=None, ge=0)
    hourly_rate: Optional[float] = Field(default=None, ge=0)
    allowance: Optional[float] = Field(default=None, ge=0)
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None
    payment_details_on_file: Optional[bool] = None


class AttendanceIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    hours_worked: float = Field(default=0, ge=0, le=400)
    overtime_hours: float = Field(default=0, ge=0, le=200)
    bonus: float = Field(default=0, ge=0)


class LeaveIn(BaseModel):
    start_date: date
    end_date: date
    days: float = Field(gt=0, le=60)
    leave_type: str = Field(default="Annual", max_length=40)
    paid: bool = True


class PeriodIn(BaseModel):
    period: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    jurisdiction: str = Field(default="GENERIC", max_length=20)
    kind: Literal["EMPLOYEE_PCT", "EMPLOYER_PCT", "EMPLOYEE_FIXED", "EMPLOYER_FIXED"]
    rate: float = Field(ge=0, le=100000)
    wage_ceiling: Optional[float] = Field(default=None, gt=0)
    applies_to: Literal["ALL", "MONTHLY", "HOURLY"] = "ALL"
    version: str = Field(default="v1", max_length=30)
    verified: bool = False
    source_note: Optional[str] = Field(default=None, max_length=300)
    active: bool = True


@endpoint(perm="employees.read")
def emp_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"employees": payroll.list_employees(conn, ctx.business_id, ctx.can("salary.read")),
                "salary_visible": ctx.can("salary.read")}


@endpoint(perm="employees.write", body=EmployeeIn)
def emp_create(request, ctx: Ctx, body: EmployeeIn):
    with db.connect() as conn:
        if db.one(conn, "SELECT 1 FROM employees WHERE business_id=? AND emp_code=?", (ctx.business_id, body.emp_code)):
            raise bad_request("Employee code already exists")
        data = body.model_dump()
        data["start_date"] = iso(body.start_date) if body.start_date else None
        data["payment_details_on_file"] = int(body.payment_details_on_file)
        eid = db.insert(conn, "employees", {"business_id": ctx.business_id, **data})
        audit(conn, ctx.business_id, "USER", ctx.actor, "employee.create", "employee", eid, {"name": body.name})
        return db.get_owned(conn, "employees", eid, ctx.business_id)


@endpoint(perm="employees.write", body=EmployeeUpdate)
def emp_update(request, ctx: Ctx, body: EmployeeUpdate):
    eid = int_param(request, "id")
    data = body.model_dump(exclude_none=True)
    with db.connect() as conn:
        e = db.get_owned(conn, "employees", eid, ctx.business_id)
        if not e:
            raise not_found("Employee")
        salary_change = any(k in data and data[k] != e[k] for k in ("base_salary", "hourly_rate", "allowance"))
        if salary_change and ctx.role != "OWNER":
            raise forbidden("Salary changes require the owner")
        if "payment_details_on_file" in data:
            data["payment_details_on_file"] = int(data["payment_details_on_file"])
        db.update(conn, "employees", eid, data, ctx.business_id)
        audit(conn, ctx.business_id, "USER", ctx.actor, "employee.salary_change" if salary_change else "employee.update", "employee", eid,
              {k: (v if not salary_change or k not in ("base_salary", "hourly_rate", "allowance") else {"from": e[k], "to": v}) for k, v in data.items()})
        return db.get_owned(conn, "employees", eid, ctx.business_id)


@endpoint(perm="employees.write", body=AttendanceIn)
def emp_attendance(request, ctx: Ctx, body: AttendanceIn):
    with db.connect() as conn:
        return payroll.upsert_attendance(conn, ctx.business_id, int_param(request, "id"), body.period, body.hours_worked,
                                         body.overtime_hours, body.bonus, ctx.actor)


@endpoint(perm="employees.write", body=LeaveIn)
def emp_leave(request, ctx: Ctx, body: LeaveIn):
    with db.connect() as conn:
        return payroll.add_leave(conn, ctx.business_id, int_param(request, "id"),
                                 {"start_date": iso(body.start_date), "end_date": iso(body.end_date), "days": body.days,
                                  "leave_type": body.leave_type, "paid": int(body.paid)}, ctx.actor)


@endpoint(perm="employees.read")
def emp_detail(request, ctx: Ctx, _):
    eid = int_param(request, "id")
    with db.connect() as conn:
        e = db.get_owned(conn, "employees", eid, ctx.business_id)
        if not e:
            raise not_found("Employee")
        if not ctx.can("salary.read"):
            for k in ("base_salary", "hourly_rate", "allowance"):
                e[k] = None
        e["attendance"] = db.all_(conn, "SELECT * FROM attendance_records WHERE employee_id=? AND business_id=? ORDER BY period DESC LIMIT 12",
                                  (eid, ctx.business_id))
        e["leave"] = db.all_(conn, "SELECT * FROM leave_records WHERE employee_id=? AND business_id=? ORDER BY start_date DESC LIMIT 20",
                             (eid, ctx.business_id))
        return e


@endpoint(perm="salary.read")
def payroll_overview(request, ctx: Ctx, _):
    with db.connect() as conn:
        nxt = payroll.next_payroll_date(conn, ctx.business_id)
        period = request.query_params.get("period") or month_key(nxt)
        return {"next_pay_date": iso(nxt), "period": period, "estimate": payroll.estimate(conn, ctx.business_id, period),
                "runs": payroll.list_runs(conn, ctx.business_id), "rules": payroll.active_rules(conn, ctx.business_id),
                "all_rules": db.all_(conn, "SELECT * FROM payroll_rules WHERE business_id=? ORDER BY id", (ctx.business_id,)),
                "templates": list(payroll.RULE_TEMPLATES)}


@endpoint(perm="payroll.prepare", body=PeriodIn)
def payroll_prepare(request, ctx: Ctx, body: PeriodIn):
    with db.connect() as conn:
        period = body.period or month_key(payroll.next_payroll_date(conn, ctx.business_id))
        return payroll.prepare(conn, ctx.business_id, period, ctx.actor)


@endpoint(perm="salary.read")
def payroll_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        return payroll.get_run(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="payroll.approve")
def payroll_approve(request, ctx: Ctx, _):
    rid = int_param(request, "id")
    with db.connect() as conn:
        aid = _approval_for(conn, ctx.business_id, "PAYROLL", rid)
        if aid:
            return approvals.decide(conn, ctx.business_id, aid, True, ctx.actor, ctx.role)["result"]
        return payroll.approve_run(conn, ctx.business_id, rid, ctx.actor, ctx.role)


@endpoint(perm="payroll.pay")
def payroll_pay(request, ctx: Ctx, _):
    with db.connect() as conn:
        return payroll.record_payment(conn, ctx.business_id, int_param(request, "id"), ctx.actor, ctx.role)


@endpoint(perm="salary.read")
def payroll_payslips(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"payslips": payroll.payslips(conn, ctx.business_id, int_param(request, "id"))}


@endpoint(perm="payroll.approve", body=RuleIn)
def rule_create(request, ctx: Ctx, body: RuleIn):
    with db.connect() as conn:
        rid = db.insert(conn, "payroll_rules", {"business_id": ctx.business_id, **body.model_dump(), "verified": int(body.verified),
                                                "active": int(body.active)})
        audit(conn, ctx.business_id, "USER", ctx.actor, "payroll_rule.create", "payroll_rule", rid, body.model_dump())
        return db.get_owned(conn, "payroll_rules", rid, ctx.business_id)


class RuleToggle(BaseModel):
    active: Optional[bool] = None
    verified: Optional[bool] = None
    rate: Optional[float] = Field(default=None, ge=0)


@endpoint(perm="payroll.approve", body=RuleToggle)
def rule_update(request, ctx: Ctx, body: RuleToggle):
    rid = int_param(request, "id")
    data = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump(exclude_none=True).items()}
    with db.connect() as conn:
        if not db.get_owned(conn, "payroll_rules", rid, ctx.business_id):
            raise not_found("Rule")
        db.update(conn, "payroll_rules", rid, data, ctx.business_id)
        audit(conn, ctx.business_id, "USER", ctx.actor, "payroll_rule.update", "payroll_rule", rid, data)
        return db.get_owned(conn, "payroll_rules", rid, ctx.business_id)


class TemplateIn(BaseModel):
    template: str


@endpoint(perm="payroll.approve", body=TemplateIn)
def rule_template(request, ctx: Ctx, body: TemplateIn):
    tpl = payroll.RULE_TEMPLATES.get(body.template)
    if not tpl:
        raise bad_request("Unknown template")
    with db.connect() as conn:
        for r in tpl:
            db.insert(conn, "payroll_rules", {"business_id": ctx.business_id, **r, "active": 0})
        audit(conn, ctx.business_id, "USER", ctx.actor, "payroll_rule.template", "business", ctx.business_id, {"template": body.template})
        return {"added": len(tpl), "note": "Template rules were added INACTIVE and UNVERIFIED. Verify rates with official sources before activating."}


# ============================================================ contracts
class ContractUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    counterparty: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    value: Optional[float] = None
    payment_terms: Optional[str] = None
    auto_renew: Optional[bool] = None
    notice_days: Optional[int] = Field(default=None, ge=0, le=365)


@endpoint(perm="contracts.read")
def con_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"contracts": contracts.list_contracts(conn, ctx.business_id), "categories": contracts.CATEGORIES,
                "reminders": contracts.upcoming_reminders(conn, ctx.business_id), "disclaimer": contracts.DISCLAIMER}


@endpoint(perm="contracts.read")
def con_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        return contracts.get_contract(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="contracts.read")
def con_summary(request, ctx: Ctx, _):
    with db.connect() as conn:
        c = contracts.get_contract(conn, ctx.business_id, int_param(request, "id"))
        return {k: c[k] for k in ("id", "title", "summary", "key_clauses", "payment_terms", "termination_clause", "auto_renew",
                                  "notice_days", "notice_deadline", "end_date", "disclaimer", "extraction_source")}


@endpoint(perm="contracts.read")
def con_expiring(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"contracts": contracts.expiring(conn, ctx.business_id, query_int(request, "days", 30, 1, 365))}


@endpoint(perm="contracts.read")
def con_search(request, ctx: Ctx, _):
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        raise bad_request("Search term too short")
    with db.connect() as conn:
        return {"results": contracts.search(conn, ctx.business_id, q)}


class ContractActionIn(BaseModel):
    action: Literal["EXTEND", "END_AT_EXPIRY"]
    months: Optional[int] = Field(default=None, ge=1, le=60)
    note: Optional[str] = Field(default=None, max_length=500)


@endpoint(perm="contracts.write", body=ContractActionIn)
def con_action(request, ctx: Ctx, body: ContractActionIn):
    with db.connect() as conn:
        return contracts.propose_action(conn, ctx.business_id, int_param(request, "id"), body.action, ctx.actor,
                                        body.months, body.note or "")


@endpoint(perm="contracts.write", body=ContractUpdate)
def con_update(request, ctx: Ctx, body: ContractUpdate):
    data = body.model_dump(exclude_none=True)
    for k in ("start_date", "end_date"):
        if k in data:
            data[k] = iso(data[k])
    if "auto_renew" in data:
        data["auto_renew"] = int(data["auto_renew"])
    with db.connect() as conn:
        return contracts.update_contract(conn, ctx.business_id, int_param(request, "id"), data, ctx.actor)


@endpoint(perm="contracts.write", limit=10)
async def con_upload(request: Request, ctx: Ctx, _):
    filename, mime, data, form = await _read_upload(request)
    from starlette.concurrency import run_in_threadpool

    def work():
        pages = contracts.extract_text(filename, data)
        if not "".join(pages).strip():
            raise bad_request("No text could be extracted (scanned PDFs are not supported in this MVP)")
        meta = features.extract_contract(ctx.business_id, pages, filename)
        with db.connect() as conn:
            path = _save_upload(ctx.business_id, filename, data)
            did = contracts.store_document(conn, ctx.business_id, filename, mime, path, pages, ctx.actor)
            c = contracts.create_contract(conn, ctx.business_id, meta, did, ctx.actor)
            insights.refresh(conn, ctx.business_id)
            return {"contract": c, "pages": len(pages), "extraction_source": meta.get("extraction_source")}
    return await run_in_threadpool(work)


@endpoint(perm="contracts.read")
def doc_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        return contracts.document_pages(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="contracts.read")
def doc_file(request, ctx: Ctx, _):
    with db.connect() as conn:
        d = db.get_owned(conn, "documents", int_param(request, "id"), ctx.business_id)
    if not d or not d["storage_path"] or not os.path.exists(d["storage_path"]):
        raise not_found("Document file")
    return FileResponse(d["storage_path"], filename=d["filename"], media_type=d["mime"] or "application/octet-stream")


# ============================================================ assistant
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: Optional[int] = None


@endpoint(perm="assistant.use", body=ChatIn, limit=config.ASSISTANT_RATE_LIMIT_PER_MIN)
def chat(request, ctx: Ctx, body: ChatIn):
    with db.connect() as conn:
        sid = body.session_id
        if sid:
            s = db.one(conn, "SELECT * FROM agent_sessions WHERE id=? AND business_id=? AND user_id=?", (sid, ctx.business_id, ctx.user_id))
            if not s:
                raise not_found("Session")
        else:
            sid = db.insert(conn, "agent_sessions", {"business_id": ctx.business_id, "user_id": ctx.user_id, "title": body.message[:80]})
        mid = db.insert(conn, "agent_messages", {"session_id": sid, "role": "user", "content": body.message})
        last = db.scalar(conn, "SELECT COALESCE(MAX(id),0) FROM agent_events WHERE session_id=?", (sid,))
    run_id = orchestrator.start(ctx, sid, body.message)
    return {"session_id": sid, "run_id": run_id, "user_message_id": mid, "events_after": last, "mode": config.ai_mode()}


@endpoint(perm="assistant.use")
def sessions(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"sessions": db.all_(conn, """SELECT s.id, s.title, s.created_at, (SELECT COUNT(*) FROM agent_messages m WHERE m.session_id=s.id) AS messages
                                             FROM agent_sessions s WHERE s.business_id=? AND s.user_id=? ORDER BY s.id DESC LIMIT 30""",
                                       (ctx.business_id, ctx.user_id))}


def _own_session(conn, ctx, sid):
    s = db.one(conn, "SELECT * FROM agent_sessions WHERE id=? AND business_id=? AND user_id=?", (sid, ctx.business_id, ctx.user_id))
    if not s:
        raise not_found("Session")
    return s


@endpoint(perm="assistant.use")
def session_get(request, ctx: Ctx, _):
    sid = int_param(request, "id")
    with db.connect() as conn:
        s = _own_session(conn, ctx, sid)
        s["messages"] = db.all_(conn, "SELECT * FROM agent_messages WHERE session_id=? ORDER BY id", (sid,))
        return s


@endpoint(perm="assistant.use")
def session_events(request, ctx: Ctx, _):
    sid = int_param(request, "id")
    after = query_int(request, "after", 0, 0, 10 ** 12)
    run_id = request.query_params.get("run_id")
    with db.connect() as conn:
        _own_session(conn, ctx, sid)
        sql = "SELECT * FROM agent_events WHERE session_id=? AND id>?"
        params = [sid, after]
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        events = db.all_(conn, sql + " ORDER BY id LIMIT 500", params)
    status = orchestrator.run_status(run_id) if run_id else None
    return {"events": events, "run": status}


@endpoint(perm="assistant.use", limit=10)
async def voice(request: Request, ctx: Ctx, _):
    if config.ai_mode() != "real":
        raise ApiError(503, "Voice input needs Muse Voice Transcribe — set MODEL_API_KEY to enable it (mock mode is active).", "voice_unavailable")
    filename, mime, data, _form = await _read_upload(request, "audio")
    if not data[:4] == b"RIFF":
        raise bad_request("Audio must be WAV (the app records WAV automatically)")
    from starlette.concurrency import run_in_threadpool
    try:
        text = await run_in_threadpool(meta_client.transcribe, data, biz=ctx.business_id)
    except meta_client.MetaAPIError as e:
        raise ApiError(502, str(e), "transcription_failed")
    return {"text": text, "model": config.META_ASR_MODEL}


@endpoint(perm="assistant.use")
def ai_status(request, ctx: Ctx, _):
    with db.connect() as conn:
        stats = db.one(conn, """SELECT COUNT(*) AS calls, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok,
                                       ROUND(AVG(latency_ms)) AS avg_latency_ms, SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)) AS tokens
                                FROM ai_requests WHERE business_id=?""", (ctx.business_id,))
    return {"mode": config.ai_mode(), "model": config.META_MODEL, "asr_model": config.META_ASR_MODEL, "stats": stats,
            "agents": [{"key": k, **v} for k, v in orchestrator.AGENTS.items()]}


@endpoint(perm="audit.read")
def ai_requests(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"requests": db.all_(conn, "SELECT * FROM ai_requests WHERE business_id=? ORDER BY id DESC LIMIT ?",
                                    (ctx.business_id, query_int(request, "limit", 50, 1, 500)))}


# ============================================================ insights
class RecStatus(BaseModel):
    status: Literal["PENDING", "ACCEPTED", "REJECTED", "COMPLETED"]


@endpoint(perm="insights.read")
def ins_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        if request.query_params.get("refresh") == "1" or not db.scalar(conn, "SELECT COUNT(*) FROM recommendations WHERE business_id=?", (ctx.business_id,)):
            insights.refresh(conn, ctx.business_id)
        return {"recommendations": insights.list_(conn, ctx.business_id, request.query_params.get("status")),
                "categories": insights.CATEGORIES}


@endpoint(perm="insights.read")
def ins_refresh(request, ctx: Ctx, _):
    with db.connect() as conn:
        return insights.refresh(conn, ctx.business_id)


@endpoint(perm="insights.write", body=RecStatus)
def ins_status(request, ctx: Ctx, body: RecStatus):
    rid = int_param(request, "id")
    with db.connect() as conn:
        if not db.get_owned(conn, "recommendations", rid, ctx.business_id):
            raise not_found("Recommendation")
        db.update(conn, "recommendations", rid, {"status": body.status}, ctx.business_id)
        audit(conn, ctx.business_id, "USER", ctx.actor, "recommendation.status", "recommendation", rid, {"status": body.status})
        return db.get_owned(conn, "recommendations", rid, ctx.business_id)


@endpoint(perm="insights.read", limit=15)
def ins_explain(request, ctx: Ctx, _):
    with db.connect() as conn:
        return features.explain_recommendation(conn, ctx.business_id, int_param(request, "id"))


# ============================================================ automations
class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    trigger_type: str
    params: dict = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    approval_required: bool = True
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    params: Optional[dict] = None
    actions: Optional[list[str]] = None
    approval_required: Optional[bool] = None


def _rule_payload(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out[k] = json.dumps(v) if k in ("params", "actions") else (int(v) if isinstance(v, bool) else v)
    return out


@endpoint(perm="automations.read")
def auto_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"rules": automations.list_rules(conn, ctx.business_id), "triggers": automations.TRIGGERS, "actions": automations.ACTIONS,
                "settings": {k: settings(conn, ctx.business_id)[k] for k in ("auto_po_enabled", "auto_approve_limit", "manager_spending_limit")}}


@endpoint(perm="automations.write", body=RuleCreate)
def auto_create(request, ctx: Ctx, body: RuleCreate):
    with db.connect() as conn:
        return automations.create_rule(conn, ctx.business_id, _rule_payload(body.model_dump()), ctx.actor)


@endpoint(perm="automations.write", body=RuleUpdate)
def auto_update(request, ctx: Ctx, body: RuleUpdate):
    with db.connect() as conn:
        return automations.update_rule(conn, ctx.business_id, int_param(request, "id"), _rule_payload(body.model_dump(exclude_none=True)), ctx.actor)


@endpoint(perm="automations.write")
def auto_toggle(request, ctx: Ctx, _):
    with db.connect() as conn:
        return automations.toggle(conn, ctx.business_id, int_param(request, "id"), ctx.actor)


@endpoint(perm="automations.write", limit=20)
def auto_run(request, ctx: Ctx, _):
    with db.connect() as conn:
        return automations.run_one(conn, ctx.business_id, int_param(request, "id"))


@endpoint(perm="automations.write", limit=10)
def auto_run_all(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"runs": automations.run_all(conn, ctx.business_id, "manual")}


@endpoint(perm="automations.read")
def auto_history(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"runs": automations.history(conn, ctx.business_id, query_int(request, "limit", 50, 1, 200))}


# ============================================================ approvals
@endpoint(perm="approvals.read")
def appr_list(request, ctx: Ctx, _):
    status = request.query_params.get("status", "PENDING")
    with db.connect() as conn:
        rows = approvals.list_(conn, ctx.business_id, None if status == "ALL" else status)
        for r in rows:
            r["can_decide"] = approvals.ROLE_RANK.get(ctx.role, 0) >= approvals.ROLE_RANK.get(r["required_role"], 3) and \
                ctx.can("procurement.approve" if r["kind"] == "PURCHASE_ORDER" else "payroll.approve")
        return {"approvals": rows}


@endpoint(perm="approvals.read", body=ReasonIn)
def appr_approve(request, ctx: Ctx, body: ReasonIn):
    with db.connect() as conn:
        a = approvals.get(conn, ctx.business_id, int_param(request, "id"))
        ctx.require("procurement.approve" if a["kind"] == "PURCHASE_ORDER" else "payroll.approve")
        out = approvals.decide(conn, ctx.business_id, a["id"], True, ctx.actor, ctx.role, body.reason)
        insights.refresh(conn, ctx.business_id)
        return out


@endpoint(perm="approvals.read", body=ReasonIn)
def appr_reject(request, ctx: Ctx, body: ReasonIn):
    with db.connect() as conn:
        a = approvals.get(conn, ctx.business_id, int_param(request, "id"))
        ctx.require("procurement.approve" if a["kind"] == "PURCHASE_ORDER" else "payroll.approve")
        return approvals.decide(conn, ctx.business_id, a["id"], False, ctx.actor, ctx.role, body.reason)


# ============================================================ reports & audit
class ReportIn(BaseModel):
    kind: Literal["FINANCIAL", "INVENTORY", "PAYROLL", "SUPPLIER", "PERFORMANCE"]
    period: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    ai_summary: bool = True


REPORT_PERMS = {"FINANCIAL": "finance.read", "INVENTORY": "inventory.read", "PAYROLL": "salary.read", "SUPPLIER": "procurement.read",
                "PERFORMANCE": "finance.read"}


@endpoint(perm="reports.read")
def rep_list(request, ctx: Ctx, _):
    with db.connect() as conn:
        return {"reports": reports.list_(conn, ctx.business_id), "kinds": reports.KINDS}


@endpoint(perm="reports.read", body=ReportIn, limit=10)
def rep_generate(request, ctx: Ctx, body: ReportIn):
    ctx.require(REPORT_PERMS[body.kind])
    with db.connect() as conn:
        data = reports.build(conn, ctx.business_id, body.kind, body.period)
    narrative, src = (features.report_narrative(ctx.business_id, reports.KINDS[body.kind], data) if body.ai_summary else (None, None))
    with db.connect() as conn:
        return reports.generate(conn, ctx.business_id, body.kind, body.period, ctx.actor, narrative, src)


@endpoint(perm="reports.read")
def rep_get(request, ctx: Ctx, _):
    with db.connect() as conn:
        r = reports.get(conn, ctx.business_id, int_param(request, "id"))
        ctx.require(REPORT_PERMS[r["kind"]])
        return r


@endpoint(perm="reports.read")
def rep_export(request, ctx: Ctx, _):
    fmt = request.query_params.get("format", "csv")
    with db.connect() as conn:
        r = reports.get(conn, ctx.business_id, int_param(request, "id"))
        ctx.require(REPORT_PERMS[r["kind"]])
        b = business(conn, ctx.business_id)
    name = re.sub(r"[^A-Za-z0-9]+", "_", r["title"]).strip("_")
    if fmt == "pdf":
        return Response(reports.to_pdf(r, b["name"], b["currency"]), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return Response(reports.to_csv(r), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@endpoint(perm="audit.read")
def audit_list(request, ctx: Ctx, _):
    q = request.query_params
    where, params = ["business_id=?"], [ctx.business_id]
    if q.get("actor_type"):
        where.append("actor_type=?")
        params.append(q["actor_type"])
    with db.connect() as conn:
        total = db.scalar(conn, f"SELECT COUNT(*) FROM audit_logs WHERE {' AND '.join(where)}", params)
        rows = db.all_(conn, f"SELECT * FROM audit_logs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
                       params + [query_int(request, "limit", 50, 1, 200), query_int(request, "offset", 0, 0, 10 ** 6)])
    return {"items": rows, "total": total}


@endpoint(auth=False)
def health(request, ctx, _):
    return {"ok": True, "ai_mode": config.ai_mode(), "model": config.META_MODEL}


R = Route
routes = [
    R("/api/health", health),
    R("/api/auth/register", register, methods=["POST"]), R("/api/auth/login", login, methods=["POST"]),
    R("/api/auth/logout", logout, methods=["POST"]), R("/api/auth/me", me), R("/api/auth/switch", switch_business, methods=["POST"]),
    R("/api/business", get_business), R("/api/business/settings", put_settings, methods=["PUT"]),
    R("/api/business/reset-demo", reset_demo_route, methods=["POST"]),
    R("/api/dashboard/overview", dash_overview), R("/api/dashboard/alerts", dash_alerts), R("/api/dashboard/briefing", dash_briefing),
    R("/api/inventory", inv_list), R("/api/inventory", inv_create, methods=["POST"]), R("/api/inventory/low-stock", inv_low),
    R("/api/inventory/{id:int}", inv_update, methods=["PUT"]), R("/api/inventory/{id:int}", inv_delete, methods=["DELETE"]),
    R("/api/inventory/{id:int}/adjust", inv_adjust, methods=["POST"]), R("/api/inventory/{id:int}/forecast", inv_forecast),
    R("/api/inventory/{id:int}/history", inv_history), R("/api/inventory/{id:int}/quotes", quotes),
    R("/api/suppliers", sup_list), R("/api/suppliers", sup_create, methods=["POST"]), R("/api/suppliers/{id:int}", sup_get),
    R("/api/suppliers/{id:int}/products", sup_add_product, methods=["POST"]),
    R("/api/purchase-orders", po_list), R("/api/purchase-orders", po_create, methods=["POST"]), R("/api/purchase-orders/{id:int}", po_get),
    R("/api/purchase-orders/{id:int}/approve", po_approve, methods=["POST"]), R("/api/purchase-orders/{id:int}/receive", po_receive, methods=["POST"]),
    R("/api/purchase-orders/{id:int}/cancel", po_cancel, methods=["POST"]),
    R("/api/procurement/restock-plan", restock_plan), R("/api/procurement/draft-restock", draft_restock, methods=["POST"]),
    R("/api/finance/summary", fin_summary), R("/api/finance/transactions", fin_transactions),
    R("/api/finance/transactions", fin_create_txn, methods=["POST"]), R("/api/finance/transactions/{id:int}", fin_update_txn, methods=["PUT"]),
    R("/api/finance/transactions/{id:int}/receipt", fin_receipt, methods=["POST"]),
    R("/api/finance/cash-flow", fin_cashflow), R("/api/finance/forecast", fin_forecast), R("/api/finance/invoices", fin_invoices),
    R("/api/finance/invoices", fin_create_invoice, methods=["POST"]), R("/api/finance/invoices/{id:int}/pay", fin_pay_invoice, methods=["POST"]),
    R("/api/finance/meta", fin_meta),
    R("/api/employees", emp_list), R("/api/employees", emp_create, methods=["POST"]), R("/api/employees/{id:int}", emp_detail),
    R("/api/employees/{id:int}", emp_update, methods=["PUT"]), R("/api/employees/{id:int}/attendance", emp_attendance, methods=["POST"]),
    R("/api/employees/{id:int}/leave", emp_leave, methods=["POST"]),
    R("/api/payroll", payroll_overview), R("/api/payroll/prepare", payroll_prepare, methods=["POST"]),
    R("/api/payroll/{id:int}", payroll_get), R("/api/payroll/{id:int}/approve", payroll_approve, methods=["POST"]),
    R("/api/payroll/{id:int}/pay", payroll_pay, methods=["POST"]), R("/api/payroll/{id:int}/payslips", payroll_payslips),
    R("/api/payroll/rules", rule_create, methods=["POST"]), R("/api/payroll/rules/{id:int}", rule_update, methods=["PUT"]),
    R("/api/payroll/rules/template", rule_template, methods=["POST"]),
    R("/api/contracts", con_list), R("/api/contracts/upload", con_upload, methods=["POST"]), R("/api/contracts/expiring", con_expiring),
    R("/api/contracts/search", con_search), R("/api/contracts/{id:int}", con_get), R("/api/contracts/{id:int}", con_update, methods=["PUT"]),
    R("/api/contracts/{id:int}/summary", con_summary), R("/api/contracts/{id:int}/actions", con_action, methods=["POST"]),
    R("/api/documents/{id:int}", doc_get), R("/api/documents/{id:int}/file", doc_file),
    R("/api/assistant/chat", chat, methods=["POST"]), R("/api/assistant/voice", voice, methods=["POST"]),
    R("/api/assistant/sessions", sessions), R("/api/assistant/sessions/{id:int}", session_get),
    R("/api/assistant/sessions/{id:int}/events", session_events), R("/api/assistant/status", ai_status),
    R("/api/ai/requests", ai_requests),
    R("/api/insights", ins_list), R("/api/insights/refresh", ins_refresh, methods=["POST"]),
    R("/api/insights/{id:int}", ins_status, methods=["PUT"]), R("/api/insights/{id:int}/explain", ins_explain, methods=["POST"]),
    R("/api/automations", auto_list), R("/api/automations", auto_create, methods=["POST"]),
    R("/api/automations/history", auto_history), R("/api/automations/run-all", auto_run_all, methods=["POST"]),
    R("/api/automations/{id:int}", auto_update, methods=["PUT"]), R("/api/automations/{id:int}/toggle", auto_toggle, methods=["POST"]),
    R("/api/automations/{id:int}/run", auto_run, methods=["POST"]),
    R("/api/approvals", appr_list), R("/api/approvals/{id:int}/approve", appr_approve, methods=["POST"]),
    R("/api/approvals/{id:int}/reject", appr_reject, methods=["POST"]),
    R("/api/reports", rep_list), R("/api/reports/generate", rep_generate, methods=["POST"]), R("/api/reports/{id:int}", rep_get),
    R("/api/reports/{id:int}/export", rep_export),
    R("/api/audit", audit_list),
]
_ = Role
