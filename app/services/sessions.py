from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppSetting, AuditLog, Game, RacingSession, Simulator, User
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.core.config import settings

DEFAULT_DURATIONS = [15, 30, 60]
logger = logging.getLogger(__name__)


def configured_durations(db: Session, include_test_durations: bool = False) -> list[int]:
    setting = db.get(AppSetting, "session_durations_minutes")
    if not setting:
        values = DEFAULT_DURATIONS.copy()
        return sorted(set(values + ([1, 2] if include_test_durations and settings.short_session_test_mode else [])))
    try:
        values = [int(value.strip()) for value in setting.value.split(",") if value.strip()]
    except ValueError:
        values = DEFAULT_DURATIONS.copy()
    else:
        values = sorted(set(value for value in values if 5 <= value <= 240)) or DEFAULT_DURATIONS.copy()
    if include_test_durations and settings.short_session_test_mode:
        values.extend([1, 2])
    return sorted(set(values))


def session_view(session: RacingSession) -> dict:
    started = session.started_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining = None
    if session.status == "running" and started:
        remaining = max(0, session.contracted_minutes * 60 - int((now - started).total_seconds()))
    warning = None
    if session.status == "running" and remaining is not None:
        if remaining <= 30:
            warning = "30_seconds"
        elif remaining <= 120:
            warning = "2_minutes"
    return {
        "id": session.public_id,
        "simulator_id": session.simulator_id,
        "game_id": session.game_id,
        "operator_id": session.operator_id,
        "status": session.status,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "started_at": started.isoformat() if started else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "contracted_minutes": session.contracted_minutes,
        "effective_seconds": session.effective_seconds,
        "remaining_seconds": remaining,
        "warning": warning,
        "failure_reason": session.failure_reason,
    }


def _audit(db: Session, actor_id: int | None, action: str, session: RacingSession, details: str = ""):
    db.add(AuditLog(actor_id=actor_id, action=action,
                    details=f"session={session.public_id} {details}"[:2000]))


def begin_session(db: Session, user: User, simulator_id: int, game_id: int,
                  duration_minutes: int, steam) -> RacingSession:
    if duration_minutes not in configured_durations(db, include_test_durations=True):
        raise ValueError("Duração de sessão indisponível.")
    if db.scalar(select(RacingSession.id).where(
            RacingSession.simulator_id == simulator_id,
            RacingSession.status.in_(["waiting", "running"]))):
        raise RuntimeError("O simulador já possui uma sessão ativa.")
    simulator = db.get(Simulator, simulator_id)
    game = db.get(Game, game_id)
    if not simulator or not game or not game.is_enabled or game.name != ASSETTO_CORSA_COMPETIZIONE.name:
        raise ValueError("Simulador ou jogo indisponível.")
    readiness = steam.inspect()
    if not readiness["ready"]:
        raise RuntimeError(readiness["reason"])
    if steam.find_game_processes():
        raise RuntimeError(f"{ASSETTO_CORSA_COMPETIZIONE.name} já está em execução; não é possível iniciar outra sessão.")
    session = RacingSession(public_id=str(uuid4()), simulator_id=simulator_id,
        game_id=game_id, operator_id=user.id, status="waiting",
        created_at=datetime.now(timezone.utc), contracted_minutes=duration_minutes)
    db.add(session)
    _audit(db, user.id, "session.start_requested", session, f"duration={duration_minutes}m")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RuntimeError("O simulador já possui uma sessão ativa.") from exc
    try:
        steam.launch_game()
    except Exception as exc:
        session.status = "technical_failure"
        session.ended_at = datetime.now(timezone.utc)
        session.failure_reason = str(exc)[:1000]
        _audit(db, user.id, "session.launch_failed", session, session.failure_reason)
        logger.exception("Could not launch %s for session %s", ASSETTO_CORSA_COMPETIZIONE.name, session.public_id)
        db.commit()
        raise RuntimeError(session.failure_reason) from exc
    return session


def cancel_waiting_session(db: Session, public_id: str, user: User, steam) -> RacingSession:
    session = db.scalar(select(RacingSession).where(RacingSession.public_id == public_id))
    if not session:
        raise LookupError("Sessão não encontrada.")
    if session.status != "waiting":
        raise RuntimeError("Somente uma sessão aguardando pode ser cancelada.")
    steam.close_game()
    session.status = "cancelled"
    session.ended_at = datetime.now(timezone.utc)
    session.effective_seconds = 0
    _audit(db, user.id, "session.cancelled", session)
    db.commit()
    return session


def end_running_session(db: Session, public_id: str, user: User, steam) -> RacingSession:
    session = db.scalar(select(RacingSession).where(RacingSession.public_id == public_id))
    if not session:
        raise LookupError("Sessão não encontrada.")
    if session.status != "running":
        raise RuntimeError("A sessão não está em andamento.")
    steam.close_game()
    if steam.find_game_processes():
        raise RuntimeError("O jogo não encerrou; verifique o computador do simulador.")
    now = datetime.now(timezone.utc)
    started = session.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    session.ended_at = now
    session.effective_seconds = max(0, int((now - started).total_seconds()))
    session.status = "completed"
    _audit(db, user.id, "session.ended_by_operator", session)
    db.commit()
    return session
