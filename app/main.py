from contextlib import asynccontextmanager
import asyncio
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router
from app.api.realtime import router as realtime_router
from app.core.config import settings
from app.database.session import Base, SessionLocal, engine
from app.models import AppSetting, Game, Simulator
from app.database.migrations import (migrate_legacy_sessions, migrate_session_operational_columns,
                                    recover_open_sessions)
from app.modules.steam.integration import SteamIntegration
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.services.session_supervisor import SessionSupervisor

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=app.state.engine)
    with app.state.session_factory() as db:
        if not db.query(Simulator).first():
            db.add(Simulator(name="Simulador 1", status="not_connected", location="Estabelecimento"))
        if not db.query(Game).first():
            db.add(Game(name=ASSETTO_CORSA_COMPETIZIONE.name, platform="Steam", is_enabled=True))
        db.commit()
        if not db.get(AppSetting, "session_durations_minutes"):
            db.add(AppSetting(key="session_durations_minutes", value="15,30,60"))
            db.commit()
        migrate_session_operational_columns(db)
        migrate_legacy_sessions(db)
        recover_open_sessions(db, app.state.steam)
    supervisor = SessionSupervisor(app.state.session_factory, app.state.steam,
        guidance_duration_seconds=settings.guidance_duration_seconds,
        unresponsive_grace_seconds=60,
        transition_lock=app.state.session_transition_lock)
    app.state.supervisor = supervisor
    app.state.supervisor_task = asyncio.create_task(supervisor.run())
    yield
    app.state.supervisor_task.cancel()
    try:
        await app.state.supervisor_task
    except asyncio.CancelledError:
        pass


if not settings.secret_key:
    raise RuntimeError("Configure RACING_SECRET_KEY no arquivo .env antes de iniciar.")

app = FastAPI(title="Racing Manager", version="0.1.0", lifespan=lifespan)
app.state.engine = engine
app.state.session_factory = SessionLocal
app.state.steam = SteamIntegration(settings)
app.state.session_transition_lock = threading.RLock()
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie=settings.session_cookie,
                   https_only=settings.cookie_secure, same_site="lax", path="/", max_age=8 * 60 * 60)
app.include_router(router, prefix="/api")
app.include_router(realtime_router, prefix="/api")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend" / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/kiosk")


@app.get("/{page}", include_in_schema=False)
def page(page: str):
    if page not in {"kiosk", "admin", "operator"}:
        return RedirectResponse("/kiosk")
    return FileResponse(ROOT / "frontend" / page / "index.html")
