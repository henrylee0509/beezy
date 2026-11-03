"""Beezy ASGI application: REST API + static single-page frontend + background automation scheduler."""
from __future__ import annotations

import asyncio
import contextlib
import os
import traceback

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, db
from .api import routes as api_routes
from .seed import ensure_demo
from .services import automations


async def _scheduler():
    """Proactive monitoring: run every enabled automation for every business on an interval."""
    if config.AUTOMATION_INTERVAL_SEC <= 0:
        return
    while True:
        await asyncio.sleep(config.AUTOMATION_INTERVAL_SEC)
        try:
            def tick():
                with db.connect() as conn:
                    for b in db.all_(conn, "SELECT id FROM businesses"):
                        automations.run_all(conn, b["id"], "schedule")
            await asyncio.to_thread(tick)
        except Exception:
            traceback.print_exc()


@contextlib.asynccontextmanager
async def lifespan(app):
    db.init_db()
    if not os.getenv("BP_SKIP_DEMO"):
        with db.connect() as conn:
            ensure_demo(conn)
    task = asyncio.create_task(_scheduler())
    print(f"Beezy ready · AI mode: {config.ai_mode()} · model: {config.META_MODEL}")
    yield
    task.cancel()


async def spa(request: Request):
    return FileResponse(config.FRONTEND_DIR / "index.html")


async def api_404(request: Request):
    return JSONResponse({"error": {"code": "not_found", "message": "Unknown API route"}}, status_code=404)


def create_app() -> Starlette:
    routes = list(api_routes) + [
        Route("/api/{rest:path}", api_404, methods=["GET", "POST", "PUT", "DELETE"]),
        Mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static"),
        Route("/", spa),
    ]
    return Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(GZipMiddleware, minimum_size=1000)])


app = create_app()
