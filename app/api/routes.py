from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_user, current_user
from app.core.config import settings
from app.database.session import get_db
from app.models import Game, PriceOption, RacingSession, Simulator, User
from app.schemas.auth import LoginRequest, UserCreate
from app.schemas.session import SessionCreate
from app.services.audit import record
from app.services.auth import hash_password, verify_password

router = APIRouter()
_login_attempts: dict[str, list[float]] = {}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    import time
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


@router.get("/operator/status")
def operator_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    simulator = db.scalar(select(Simulator).order_by(Simulator.id))
    running = db.scalar(select(RacingSession).where(RacingSession.status == "running"))
    return {"simulator": simulator.name if simulator else "Simulador 1",
            "simulator_status": simulator.status if simulator else "not_connected",
            "available": running is None, "running_session": _session_view(running) if running else None,
            "today_count": len(db.scalars(select(RacingSession).where(
                RacingSession.started_at >= datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0))).all()),
            "operator": user.username}


@router.get("/pricing")
def pricing(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": p.id, "name": p.name, "duration_minutes": p.duration_minutes, "price": float(p.price)}
            for p in db.scalars(select(PriceOption).where(PriceOption.is_active.is_(True)).order_by(PriceOption.duration_minutes))]


@router.post("/sessions", status_code=201)
def start_session(payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.scalar(select(RacingSession).where(RacingSession.status == "running")):
        raise HTTPException(409, "Já existe uma sessão em andamento")
    simulator = db.get(Simulator, payload.simulator_id)
    price = db.get(PriceOption, payload.price_option_id)
    if not simulator or not price or not price.is_active:
        raise HTTPException(422, "Simulador ou preço indisponível")
    gross = Decimal(price.price)
    venue = (gross * Decimal(price.venue_share_percent) / Decimal(100)).quantize(Decimal("0.01"))
    session = RacingSession(public_id=str(uuid4()), simulator_id=simulator.id, price_option_id=price.id,
        operator_id=user.id, status="running", started_at=datetime.now(timezone.utc),
        contracted_minutes=price.duration_minutes, gross_amount=gross, venue_amount=venue, owner_amount=gross-venue)
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_view(session)


@router.post("/sessions/{public_id}/finish")
def finish_session(public_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = db.scalar(select(RacingSession).where(RacingSession.public_id == public_id, RacingSession.status == "running"))
    if not session:
        raise HTTPException(404, "Sessão em andamento não encontrada")
    ended = datetime.now(timezone.utc)
    session.ended_at = ended
    started = session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    session.effective_seconds = max(0, int((ended - started).total_seconds()))
    session.status = "completed"
    db.commit()
    return _session_view(session)


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
    gross = sum((s.gross_amount for s in sessions if s.status == "completed"), Decimal(0))
    return {"sessions_total": len(sessions), "sessions_completed": sum(s.status == "completed" for s in sessions),
            "gross_revenue": float(gross), "users": db.query(User).count(),
            "active_session": next((_session_view(s) for s in sessions if s.status == "running"), None)}


@router.get("/admin/audit")
def audit_log(db: Session = Depends(get_db), admin: User = Depends(admin_user)):
    from app.models import AuditLog
    return [{"action": a.action, "details": a.details, "created_at": a.created_at.isoformat()}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))]


def _session_view(session: RacingSession):
    started = session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    remaining = max(0, session.contracted_minutes * 60 - int((datetime.now(timezone.utc) - started).total_seconds())) if session.status == "running" else None
    return {"id": session.public_id, "status": session.status, "started_at": started.isoformat(), "remaining_seconds": remaining,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "contracted_minutes": session.contracted_minutes, "effective_seconds": session.effective_seconds,
            "gross_amount": float(session.gross_amount)}
