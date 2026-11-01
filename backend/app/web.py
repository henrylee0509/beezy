"""Small request-handling toolkit on top of Starlette: auth context, permission checks,
Pydantic validation, consistent JSON errors and rate limiting."""
from __future__ import annotations

import functools
import json
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

import jwt
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import config, db, security


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "error", details: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.details = details


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, f"{what} not found", "not_found")


def forbidden(msg: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, msg, "forbidden")


def bad_request(msg: str, details: Any = None) -> ApiError:
    return ApiError(400, msg, "bad_request", details)


@dataclass
class Ctx:
    user_id: int
    business_id: int
    role: str
    email: str
    name: str
    token_payload: dict

    @property
    def actor(self) -> str:
        return f"{self.name} ({self.role})"

    def can(self, perm: str) -> bool:
        return security.has_perm(self.role, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            raise forbidden(f"Your role ({self.role}) lacks permission '{perm}'")


# ---------- rate limiting (sliding window, per key) ----------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise ApiError(429, "Too many requests, please slow down", "rate_limited")
    q.append(now)


def reset_rate_limits() -> None:
    _hits.clear()


def json_default(o):
    return str(o)


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=json_default)), status_code=status)


def _auth(request: Request) -> Ctx:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not token:
        raise ApiError(401, "Authentication required", "unauthenticated")
    try:
        payload = security.decode_token(token)
    except jwt.PyJWTError:
        raise ApiError(401, "Invalid or expired session", "unauthenticated")
    with db.connect() as conn:
        row = db.one(
            conn,
            """SELECT u.id, u.email, u.name, m.role FROM users u
               JOIN business_members m ON m.user_id = u.id AND m.business_id = ?
               WHERE u.id = ?""",
            (payload["biz"], int(payload["sub"])),
        )
    if not row:
        raise ApiError(401, "Membership not found", "unauthenticated")
    return Ctx(row["id"], payload["biz"], row["role"], row["email"], row["name"], payload)


async def parse_body(request: Request, model: type[BaseModel] | None):
    if model is None:
        return None
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise bad_request("Request body must be valid JSON")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ApiError(422, "Validation failed", "validation_error",
                       [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])


def endpoint(perm: str | None = None, body: type[BaseModel] | None = None, auth: bool = True,
             limit: int | None = None):
    """Decorator: handles auth, permission, body validation, rate limiting and error mapping.

    The wrapped function receives (request, ctx, payload) and may be sync or async.
    Sync handlers run in a worker thread so SQLite/HTTP calls never block the event loop.
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(request: Request) -> Response:
            try:
                ctx = _auth(request) if auth else None
                key = f"u{ctx.user_id}" if ctx else f"ip{request.client.host if request.client else '?'}"
                rate_limit(key + ":all", config.RATE_LIMIT_PER_MIN * 5)
                if limit:
                    rate_limit(f"{key}:{fn.__name__}", limit)
                if perm and ctx:
                    ctx.require(perm)
                payload = await parse_body(request, body)
                import inspect
                if inspect.iscoroutinefunction(fn):
                    result = await fn(request, ctx, payload)
                else:
                    from starlette.concurrency import run_in_threadpool
                    result = await run_in_threadpool(fn, request, ctx, payload)
                if isinstance(result, Response):
                    return result
                return ok(result)
            except ApiError as e:
                return JSONResponse({"error": {"code": e.code, "message": e.message, "details": e.details}},
                                    status_code=e.status)
            except Exception as e:  # pragma: no cover - defensive
                traceback.print_exc()
                return JSONResponse({"error": {"code": "server_error", "message": str(e)}}, status_code=500)
        return wrapper
    return deco


def int_param(request: Request, name: str) -> int:
    try:
        return int(request.path_params[name])
    except (KeyError, ValueError):
        raise bad_request(f"Invalid {name}")


def query_int(request: Request, name: str, default: int, lo: int = 1, hi: int = 1000) -> int:
    try:
        v = int(request.query_params.get(name, default))
    except ValueError:
        raise bad_request(f"Invalid query parameter {name}")
    return max(lo, min(hi, v))
