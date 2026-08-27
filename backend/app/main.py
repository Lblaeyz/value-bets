import os
from contextlib import asynccontextmanager
from datetime import datetime

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, matches, predictions, performance, admin
from app.scheduler.jobs import scheduler, register_jobs
from app.utils.logger import logger

# ------------------------------------------------------------------ #
# Sentry (no-op when DSN is unset)
# ------------------------------------------------------------------ #
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.2)
    logger.info("Sentry initialised")


# ------------------------------------------------------------------ #
# Lifespan — start / stop scheduler
# ------------------------------------------------------------------ #
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up football-value-betting API")
    register_jobs()
    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
    yield
    logger.info("Shutting down — stopping scheduler")
    scheduler.shutdown(wait=False)


# ------------------------------------------------------------------ #
# App
# ------------------------------------------------------------------ #
app = FastAPI(
    title="Football Value Betting API",
    version="0.1.0",
    description="Value betting predictions powered by Poisson models and live odds.",
    lifespan=lifespan,
)

# ------------------------------------------------------------------ #
# CORS
# ------------------------------------------------------------------ #
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
_cors_raw = os.getenv("CORS_ORIGINS", "*")
ALLOW_ORIGINS = [
    origin.strip()
    for origin in _cors_raw.split(",")
    if origin.strip()
] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------ #
# Routers
# ------------------------------------------------------------------ #
API_PREFIX = "/api"

app.include_router(health.router,      prefix=API_PREFIX)
app.include_router(matches.router,     prefix=API_PREFIX)
app.include_router(predictions.router, prefix=API_PREFIX)
app.include_router(performance.router, prefix=API_PREFIX)
app.include_router(admin.router,       prefix=API_PREFIX)

logger.info(
    "Routes registered: %s",
    [getattr(route, "path", "?") for route in app.routes],
)
