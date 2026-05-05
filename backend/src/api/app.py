"""FastAPI application factory. Tables are created on startup; bot loops are
restored from DB so a server restart resumes any bots that were running."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import auth, bot, dashboard, data, wallet
from src.api.routers import settings as settings_router
from src.api.routers import wallets
from src.bot_manager.manager import bot_manager
from src.config.settings import settings
from src.database.base import Base
from src.database.bootstrap import ensure_columns
from src.database.session import engine
from src.utils.logging import get_logger

log = get_logger("API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    log.info("database tables ensured")
    await bot_manager.restart_all()
    log.info("API ready in mode=%s", settings.mode)
    yield
    # Shutdown: cancel all running bot loops cleanly
    await bot_manager.stop_all()
    log.info("API shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Polymarket Copy-Trade API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # NOTE: tighten allow_origins in prod to the deployed frontend URL.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(bot.router, prefix="/bot", tags=["bot"])
    app.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
    app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
    app.include_router(settings_router.router, prefix="/settings", tags=["settings"])
    app.include_router(data.router, tags=["data"])
    app.include_router(dashboard.router, tags=["dashboard"])

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "mode": settings.mode}

    return app
