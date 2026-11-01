"""Shared helpers: clock, money rounding, settings access, audit logging."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from .. import db

_today_override: date | None = None
if os.getenv("BP_TODAY"):
    _today_override = date.fromisoformat(os.environ["BP_TODAY"])


def today() -> date:
    return _today_override or date.today()


def set_today(d: date | None) -> None:  # used by tests
    global _today_override
    _today_override = d


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def iso(d: date) -> str:
    return d.isoformat()


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def money(x: float | int | None) -> float:
    return round(float(x or 0) + 1e-9, 2)


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def month_bounds(period: str) -> tuple[date, date]:
    y, m = map(int, period.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return start, end


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    from calendar import monthrange
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def settings(conn, business_id: int) -> dict:
    s = db.one(conn, "SELECT * FROM business_settings WHERE business_id = ?", (business_id,))
    if not s:
        db.insert(conn, "business_settings", {"business_id": business_id})
        s = db.one(conn, "SELECT * FROM business_settings WHERE business_id = ?", (business_id,))
    return s


def business(conn, business_id: int) -> dict:
    return db.one(conn, "SELECT * FROM businesses WHERE id = ?", (business_id,))


def audit(conn, business_id: int, actor_type: str, actor: str, action: str,
          entity: str | None = None, entity_id: int | None = None, details: dict | None = None) -> None:
    db.insert(conn, "audit_logs", {
        "business_id": business_id, "actor_type": actor_type, "actor": actor, "action": action,
        "entity": entity, "entity_id": entity_id, "details": json.dumps(details or {}, default=str),
    })


def actor_type_for(actor: str) -> str:
    if actor.startswith("agent:"):
        return "AGENT"
    if actor.startswith("automation:"):
        return "AUTOMATION"
    if actor == "system":
        return "SYSTEM"
    return "USER"
