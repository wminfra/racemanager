import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database.session import SessionLocal
from app.models import RacingSession, User
from app.api.routes import _session_view

router = APIRouter()


@router.websocket("/ws/session")
async def session_updates(websocket: WebSocket):
    user_id = websocket.session.get("user_id")
    with SessionLocal() as db:
        user = db.get(User, user_id) if user_id else None
        if not user or not user.is_active or user.role not in {"admin", "operator"}:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    try:
        while True:
            with SessionLocal() as db:
                session = db.scalar(select(RacingSession).where(RacingSession.status == "running"))
                payload = {"type": "session_status", "running_session": _session_view(session) if session else None}
            await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
