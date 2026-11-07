from contextlib import asynccontextmanager
import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router
from app.api.realtime import router as realtime_router
from app.core.config import settings
from app.database.session import Base, SessionLocal, engine
from app.models import Game, PriceOption, Simulator
from app.services.session_timer import expire_sessions

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(Simulator).first():
            db.add(Simulator(name="Simulador 1", status="not_connected", location="Estabelecimento"))
        if not db.query(Game).first():
            db.add(Game(name="Assetto Corsa", platform="Steam", is_enabled=True))
        if not db.query(PriceOption).first():
            db.add_all([PriceOption(name="15 minutos", duration_minutes=15, price=25, venue_share_percent=50),
                        PriceOption(name="30 minutos", duration_minutes=30, price=45, venue_share_percent=50)])
        db.commit()
    timer_task = asyncio.create_task(expire_sessions())
    yield
    timer_task.cancel()
    try:
        await timer_task
    except asyncio.CancelledError:
        pass


if not settings.secret_key:
    raise RuntimeError("Configure RACING_SECRET_KEY no arquivo .env antes de iniciar.")

app = FastAPI(title="Racing Manager", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie=settings.session_cookie,
                   https_only=settings.cookie_secure, same_site="lax", max_age=8 * 60 * 60)
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
