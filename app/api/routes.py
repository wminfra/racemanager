import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_user, current_user
from app.core.config import settings
from app.database.session import get_db
from app.models import AppSetting, AuditLog, Game, RacingSession, Simulator, User
from app.schemas.auth import LoginRequest, UserCreate
from app.schemas.session import SessionCreate, SessionDurationsUpdate
from app.services.audit import record
from app.services.auth import hash_password, verify_password
from app.services.sessions import (begin_session, cancel_waiting_session, configured_durations,
                                   end_running_session, session_view)

router = APIRouter()
_login_attempts: dict[str, list[float]] = {}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = [t for t in _login_attempts.get(host, []) if now - t < settings.login_window_seconds]
    _login_attempts[host] = attempts
    if len(attempts) >= settings.login_max_attempts:
        raise HTTPException(429, "Muitas tentativas. Aguarde antes de tentar novamente.")
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        attempts.append(now)
        _login_attempts[host] = attempts
        raise HTTPException(401, "Usuário ou senha inválidos")
    _login_attempts.pop(host, None)
    request.session.clear()
    request.session["user_id"] = user.id
    return {"id": user.id, "username": user.username, "role": user.role}


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


def _active_session(db: Session, simulator_id: int = 1):
    return db.scalar(select(RacingSession).where(
        RacingSession.simulator_id == simulator_id,
        RacingSession.status.in_(["waiting", "running"])))


def _simulator_status(db: Session, simulator_id: int, steam) -> dict:
    simulator = db.get(Simulator, simulator_id)
    active = _active_session(db, simulator_id)
    readiness = steam.inspect()
    game_running = bool(steam.find_game_processes())
    if active and active.status == "running":
        state = "game_running"
    elif active:
        state = "starting"
    elif game_running:
        state = "unmanaged_game_running"
    elif readiness["ready"]:
        state = "ready"
    else:
        state = "not_connected"
    safe_readiness = {key: readiness[key] for key in
                      ("steam_installed", "game_installed", "steam_running", "account_selected",
                       "ready", "app_id", "reason")}
    return {"simulator": simulator.name if simulator else f"Simulador {simulator_id}",
            "simulator_id": simulator_id, "simulator_status": state,
            "available": active is None and readiness["ready"] and not game_running,
            "game_process_detected": game_running,
            "session": session_view(active) if active else None,
            "steam": safe_readiness}


@router.get("/operator/status")
def operator_status(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    status = _simulator_status(db, 1, request.app.state.steam)
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    status["today_count"] = db.scalar(select(func.count(RacingSession.id)).where(
        RacingSession.created_at >= local_midnight)) or 0
    status["operator"] = user.username
    status["recent_failure"] = db.scalar(select(RacingSession).where(
        RacingSession.status == "technical_failure").order_by(RacingSession.ended_at.desc()))
    status["recent_failure"] = session_view(status["recent_failure"]) if status["recent_failure"] else None
    return status


@router.get("/sessions/durations")
def session_durations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"durations": configured_durations(db, include_test_durations=True)}


@router.get("/operator/sessions")
def operator_history(db: Session = Depends(get_db), user: User = Depends(current_user),
                     limit: int = Query(default=25, ge=1, le=100)):
    sessions = db.scalars(select(RacingSession).order_by(RacingSession.created_at.desc()).limit(limit)).all()
    return [session_view(item) for item in sessions]


@router.get("/kiosk/status")
def kiosk_status(request: Request, db: Session = Depends(get_db)):
    simulator = db.get(Simulator, 1)
    session = _active_session(db, 1)
    value = session_view(session) if session else None
    game_running = bool(request.app.state.steam.find_game_processes())
    state = "game_running" if value and value["status"] == "running" else "starting" if value else "unmanaged_game_running" if game_running else "waiting"
    return {"simulator_status": state,
            "session": {"id": value["id"], "status": value["status"],
                        "remaining_seconds": value["remaining_seconds"], "warning": value["warning"]} if value else None,
            "simulator": simulator.name if simulator else "Simulador"}


@router.post("/sessions", status_code=201)
def start_session(payload: SessionCreate, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        session = begin_session(db, user, payload.simulator_id, payload.game_id,
                                payload.duration_minutes, request.app.state.steam)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return session_view(session)


@router.post("/sessions/{public_id}/cancel")
def cancel_session(public_id: str, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        return session_view(cancel_waiting_session(db, public_id, user, request.app.state.steam))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sessions/{public_id}/end")
def finish_session(public_id: str, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        return session_view(end_running_session(db, public_id, user, request.app.state.steam))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/admin/users", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    if payload.role != "operator":
        raise HTTPException(422, "Administradores não podem ser criados por este endpoint")
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "Nome de usuário já existe")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role="operator")
    db.add(user)
    record(db, admin.id, "user.create_operator", payload.username)
    db.commit()
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/admin/summary")
def admin_summary(db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    sessions = db.scalars(select(RacingSession)).all()
    counts = {key: sum(session.status == key for session in sessions)
              for key in ("waiting", "running", "completed", "cancelled", "interrupted", "technical_failure")}
    return {"sessions_total": len(sessions), "by_status": counts,
            "operators": db.scalar(select(func.count(User.id)).where(User.role == "operator")) or 0,
            "active_session": next((session_view(s) for s in sessions if s.status in {"waiting", "running"}), None)}


@router.get("/admin/session-durations")
def read_session_durations(db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    return {"durations": configured_durations(db)}


@router.put("/admin/session-durations")
def update_session_durations(payload: SessionDurationsUpdate,
                             db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    values = sorted(set(payload.durations))
    if not values or any(value < 5 or value > 240 for value in values):
        raise HTTPException(422, "Informe durações únicas entre 5 e 240 minutos.")
    setting = db.get(AppSetting, "session_durations_minutes")
    if setting is None:
        setting = AppSetting(key="session_durations_minutes", value="")
        db.add(setting)
    setting.value = ",".join(map(str, values))
    record(db, admin.id, "settings.session_durations", setting.value)
    db.commit()
    return {"durations": values}


@router.get("/admin/audit")
def audit_log(db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    return [{"action": item.action, "details": item.details, "created_at": item.created_at.isoformat()}
            for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))]
