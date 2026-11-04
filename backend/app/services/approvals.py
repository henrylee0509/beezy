"""Human-in-the-loop approval queue. AI agents and automations can only *request* approval."""
from __future__ import annotations

from .. import db
from ..web import bad_request, forbidden, not_found
from .common import actor_type_for, audit, now_iso

ROLE_RANK = {"STAFF": 0, "INVENTORY": 1, "HR": 1, "FINANCE": 1, "MANAGER": 2, "OWNER": 3, "SYSTEM": 3}


def create(conn, biz: int, kind: str, ref_id: int, title: str, summary: str, amount: float | None,
           requested_by: str, required_role: str = "OWNER") -> int:
    existing = db.one(conn, """SELECT id FROM approval_requests WHERE business_id=? AND kind=? AND ref_id=?
                               AND status='PENDING'""", (biz, kind, ref_id))
    if existing:
        return existing["id"]
    aid = db.insert(conn, "approval_requests", {
        "business_id": biz, "kind": kind, "ref_id": ref_id, "title": title, "summary": summary,
        "amount": amount, "requested_by": requested_by, "required_role": required_role})
    audit(conn, biz, actor_type_for(requested_by), requested_by, "approval.request", "approval", aid,
          {"kind": kind, "ref_id": ref_id, "amount": amount})
    return aid


def list_(conn, biz: int, status: str | None = "PENDING") -> list[dict]:
    sql = "SELECT * FROM approval_requests WHERE business_id=?"
    params: list = [biz]
    if status:
        sql += " AND status=?"
        params.append(status)
    return db.all_(conn, sql + " ORDER BY id DESC LIMIT 200", params)


def get(conn, biz: int, aid: int) -> dict:
    a = db.get_owned(conn, "approval_requests", aid, biz)
    if not a:
        raise not_found("Approval request")
    return a


def decide(conn, biz: int, aid: int, approve: bool, actor: str, role: str, reason: str = "") -> dict:
    from . import contracts, payroll, procurement
    a = get(conn, biz, aid)
    if a["status"] != "PENDING":
        raise bad_request(f"Request already {a['status'].lower()}")
    if ROLE_RANK.get(role, 0) < ROLE_RANK.get(a["required_role"], 3):
        raise forbidden(f"This request requires {a['required_role']} approval")
    with db.transaction(conn):
        result = None
        if approve:
            if a["kind"] == "PURCHASE_ORDER":
                result = procurement.approve_and_submit(conn, biz, a["ref_id"], actor, role)
            elif a["kind"] == "PAYROLL":
                result = payroll.approve_run(conn, biz, a["ref_id"], actor, role)
            elif a["kind"] == "PAYROLL_PAYMENT":
                result = payroll.record_payment(conn, biz, a["ref_id"], actor, role)
            elif a["kind"] == "CONTRACT_ACTION":
                result = contracts.apply_action(conn, biz, a["ref_id"], actor, True)
        else:
            if a["kind"] == "PURCHASE_ORDER":
                procurement.cancel_po(conn, biz, a["ref_id"], actor, reason or "Rejected by approver")
            elif a["kind"] == "PAYROLL":
                db.update(conn, "payroll_runs", a["ref_id"], {"status": "DRAFT"}, biz)
            elif a["kind"] == "CONTRACT_ACTION":
                contracts.apply_action(conn, biz, a["ref_id"], actor, False)
        db.update(conn, "approval_requests", aid, {"status": "APPROVED" if approve else "REJECTED",
                                                   "decided_by": actor, "decided_at": now_iso(), "reason": reason}, biz)
        audit(conn, biz, actor_type_for(actor), actor, "approval.approve" if approve else "approval.reject",
              "approval", aid, {"kind": a["kind"], "ref_id": a["ref_id"], "reason": reason})
    out = get(conn, biz, aid)
    out["result"] = result
    return out
