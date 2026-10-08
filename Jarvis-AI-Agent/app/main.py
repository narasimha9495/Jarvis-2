"""Jarvis AI Agent - FastAPI application entry point."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.actions.system_monitor import prime_cpu_counters
from app.api.routes import router
from app.api.websocket import ws_router
from app.config import get_settings
from app.core.llm_router import get_llm_router
from app.core.reminder_scheduler import run_reminder_loop
from app.core.security import origin_allowed
from app.db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jarvis")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(f"Starting Jarvis AI Agent v{__version__}...")
    await init_db()
    logger.info("Database initialized.")
    prime_cpu_counters()

    llm = get_llm_router()
    logger.info(f"LLM providers: {llm.get_available_providers()} (default: {llm.default_provider_name})")

    reminder_task = asyncio.create_task(run_reminder_loop(settings.reminder_check_seconds))
    try:
        yield
    finally:
        reminder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminder_task
        logger.info("Shutting down Jarvis AI Agent.")


app = FastAPI(
    title="Jarvis AI Agent",
    description="A modular personal productivity AI assistant",
    version=__version__,
    lifespan=lifespan,
)

# The dashboard is served by this same app, so it needs no CORS at all.
# Only origins you list in ALLOWED_ORIGINS (e.g. a separate dev server) get CORS.
if get_settings().allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def block_cross_site_writes(request: Request, call_next):
    """Stop other websites from changing your data through your browser (CSRF)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_allowed(
        request.headers.get("origin"), request.headers.get("host")
    ):
        return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    return await call_next(request)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

app.include_router(router, prefix="/api")
app.include_router(ws_router)


@app.get("/", include_in_schema=False)
async def root():
    """Serve the dashboard."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Jarvis AI Agent API", "docs": "/docs"}


if __name__ == "__main__":
    settings = get_settings()
    if settings.app_host not in ("127.0.0.1", "localhost"):
        logger.warning(
            f"Jarvis is listening on {settings.app_host}: other devices on your network "
            "can reach it, and it has no login. Use APP_HOST=127.0.0.1 unless you need this."
        )
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=settings.debug)
