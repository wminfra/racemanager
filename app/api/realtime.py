import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.models import RacingSession, User
from app.services.sessions import session_view

router = APIRouter()


async def _send_status(websocket: WebSocket, public: bool = False, user_id: int | None = None):
    factory = websocket.app.state.session_factory
    while True:
        with factory() as db:
            if user_id is not None:
                user = db.get(User, user_id)
                if not user or not user.is_active or user.role not in {"admin", "operator"}:
                    await websocket.close(code=4401)
                    return
            session = db.scalar(select(RacingSession).where(
                RacingSession.simulator_id == 1,
                RacingSession.status.in_(["waiting", "running"])))
        value = session_view(session) if session else None
        if public and value:
            value = {key: value[key] for key in ("id", "status", "remaining_seconds", "warning")}
        simulator_status = "game_running" if value and value["status"] == "running" else "starting" if value else "waiting"
        if public and not value and websocket.app.state.steam.find_game_processes():
            simulator_status = "unmanaged_game_running"
        await websocket.send_json({"type": "session_status",
                                   "session" if public else "running_session": value,
                                   "simulator_status": simulator_status,
                                   "screen": "session" if value and value["status"] == "running" else "waiting"})
        await asyncio.sleep(1)


@router.websocket("/ws/session")
async def session_updates(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "").lower()
    if origin and urlsplit(origin).netloc.lower() != host:
        await websocket.close(code=4403)
        return
    user_id = websocket.session.get("user_id")
    with websocket.app.state.session_factory() as db:
        user = db.get(User, user_id) if user_id else None
        if not user or not user.is_active or user.role not in {"admin", "operator"}:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    try:
        await _send_status(websocket, user_id=user.id)
    except WebSocketDisconnect:
        return


@router.websocket("/ws/kiosk")
async def kiosk_updates(websocket: WebSocket):
    await websocket.accept()
    try:
        await _send_status(websocket, public=True)
    except WebSocketDisconnect:
        return
