"""Authentication, password hashing and role-based permissions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone

import jwt

from . import config

PBKDF2_ITERATIONS = int(os.getenv("BP_PBKDF2_ITERATIONS", "240000"))

ROLES = ("OWNER", "MANAGER", "HR", "FINANCE", "INVENTORY", "STAFF")

# permission -> roles that hold it
PERMISSIONS: dict[str, set[str]] = {
    "dashboard.read": set(ROLES),
    "inventory.read": set(ROLES),
    "inventory.write": {"OWNER", "MANAGER", "INVENTORY"},
    "procurement.read": {"OWNER", "MANAGER", "INVENTORY", "FINANCE"},
    "procurement.write": {"OWNER", "MANAGER", "INVENTORY"},
    "procurement.approve": {"OWNER", "MANAGER"},  # managers are capped by manager_spending_limit
    "finance.read": {"OWNER", "MANAGER", "FINANCE"},
    "finance.write": {"OWNER", "FINANCE"},
    "employees.read": {"OWNER", "MANAGER", "HR"},
    "salary.read": {"OWNER", "HR"},
    "employees.write": {"OWNER", "HR"},
    "payroll.prepare": {"OWNER", "HR"},
    "payroll.approve": {"OWNER"},
    "payroll.pay": {"OWNER"},
    "contracts.read": {"OWNER", "MANAGER", "HR", "FINANCE"},
    "contracts.write": {"OWNER", "MANAGER"},
    "insights.read": {"OWNER", "MANAGER", "FINANCE"},
    "insights.write": {"OWNER", "MANAGER"},
    "automations.read": {"OWNER", "MANAGER"},
    "automations.write": {"OWNER"},
    "approvals.read": {"OWNER", "MANAGER", "HR", "FINANCE", "INVENTORY"},
    "reports.read": {"OWNER", "MANAGER", "FINANCE"},
    "audit.read": {"OWNER", "MANAGER"},
    "settings.write": {"OWNER"},
    "assistant.use": set(ROLES),
}


def has_perm(role: str, perm: str) -> bool:
    return role in PERMISSIONS.get(perm, set())


def perms_for(role: str) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


# Revoked token ids (logout). In-memory is sufficient for a single-process deployment.
_revoked: dict[str, float] = {}


def issue_token(user_id: int, business_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "biz": business_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=config.JWT_TTL_HOURS)).timestamp()),
        "jti": base64.urlsafe_b64encode(os.urandom(12)).decode(),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    if payload.get("jti") in _revoked:
        raise jwt.InvalidTokenError("revoked")
    return payload


def revoke(payload: dict) -> None:
    now = time.time()
    for k, exp in list(_revoked.items()):
        if exp < now:
            _revoked.pop(k, None)
    _revoked[payload["jti"]] = float(payload.get("exp", now + 86400))
