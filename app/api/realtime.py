import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.models import RacingSession, User
from app.services.sessions import public_kiosk_state, session_view

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
            public_state = public_kiosk_state(db) if public else None
        value = session_view(session) if session else None
        if public:
            game_running = bool(websocket.app.state.steam.find_game_processes())
            simulator_status = ("game_running" if public_state["screen"] == "playing" else
                                "unmanaged_game_running" if game_running and public_state["screen"] == "waiting" else
                                "starting" if public_state["screen"] == "guidance" else "waiting")
            await websocket.send_json({"type": "session_status", **public_state,
                                       "simulator_status": simulator_status})
        else:
            phase = value["phase"] if value else None
            screen = ("playing" if value and value["status"] == "running" else
                      "guidance" if value and phase in {"guidance", "launching"} else "waiting")
            await websocket.send_json({"type": "session_status", "running_session": value,
                                       "simulator_status": "game_running" if screen == "playing" else "starting" if screen == "guidance" else "waiting",
                                       "screen": screen})
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
