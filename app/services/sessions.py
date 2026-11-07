from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppSetting, AuditLog, Game, RacingSession, Simulator, User
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.core.config import settings

DEFAULT_DURATIONS = [15, 30, 60]
GUIDANCE_MESSAGES = (
    "Ajuste o banco, o volante e os pedais antes de começar.",
    "Mantenha as mãos no volante e siga as orientações do estabelecimento.",
    "O Assetto Corsa Competizione será iniciado automaticamente.",
)
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
    created = session.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    guidance_started = session.guidance_started_at
    if guidance_started and guidance_started.tzinfo is None:
        guidance_started = guidance_started.replace(tzinfo=timezone.utc)
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
    phase = session.status
    guidance_remaining = None
    if session.status == "waiting":
        if session.launch_requested_at:
            phase = "launching"
        elif guidance_started:
            phase = "guidance"
            guidance_remaining = max(0, settings.guidance_duration_seconds -
                                     int((now - guidance_started).total_seconds()))
        else:
            phase = "launching"
    return {
        "id": session.public_id,
        "simulator_id": session.simulator_id,
        "game_id": session.game_id,
        "operator_id": session.operator_id,
        "status": session.status,
        "phase": phase,
        "created_at": created.isoformat() if created else None,
        "guidance_started_at": guidance_started.isoformat() if guidance_started else None,
        "guidance_remaining_seconds": guidance_remaining,
        "launch_requested_at": session.launch_requested_at.isoformat() if session.launch_requested_at else None,
        "started_at": started.isoformat() if started else None,
        "end_requested_at": session.end_requested_at.isoformat() if session.end_requested_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "contracted_minutes": session.contracted_minutes,
        "effective_seconds": session.effective_seconds,
        "remaining_seconds": remaining,
        "warning": warning,
        "failure_reason": session.failure_reason,
    }


def public_kiosk_state(db: Session) -> dict:
    """Return only the public screen state and fields needed by Kiosk/overlay clients."""
    now = datetime.now(timezone.utc)
    active = db.scalar(select(RacingSession).where(
        RacingSession.simulator_id == 1,
        RacingSession.status.in_(["waiting", "running"])))
    if active:
        value = session_view(active)
        phase = value["phase"]
        screen = "playing" if active.status == "running" else "guidance"
        public_session = {key: value[key] for key in (
            "id", "status", "phase", "remaining_seconds", "guidance_remaining_seconds"
        )}
        return {"screen": screen, "session": public_session,
                "guidance_messages": list(GUIDANCE_MESSAGES) if phase in {"guidance", "launching"} else []}

    terminal = db.scalar(select(RacingSession).where(
        RacingSession.simulator_id == 1,
        RacingSession.status.in_(["completed", "technical_failure", "interrupted"])
    ).order_by(RacingSession.ended_at.desc()).limit(1))
    if terminal and terminal.ended_at:
        ended = terminal.ended_at
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        age = (now - ended).total_seconds()
        display_for = (settings.session_end_display_seconds if terminal.status == "completed"
                       else settings.session_failure_display_seconds)
        if 0 <= age <= display_for:
            screen = "ending" if terminal.status == "completed" else "failure"
            return {"screen": screen, "session": {
                "id": terminal.public_id, "status": terminal.status, "phase": terminal.status,
                "remaining_seconds": 0 if terminal.status == "completed" else None,
                "guidance_remaining_seconds": None,
            }, "guidance_messages": []}
    return {"screen": "waiting", "session": None, "guidance_messages": []}


def _audit(db: Session, actor_id: int | None, action: str, session: RacingSession, details: str = ""):
    db.add(AuditLog(actor_id=actor_id, action=action,
                    details=f"session={session.public_id} {details}"[:2000]))


def begin_session(db: Session, user: User, simulator_id: int, game_id: int,
                  duration_minutes: int, steam) -> RacingSession:
    logger.info("[SESSION] Solicitação recebida.")
    logger.info("[SESSION] Simulador ID: %s.", simulator_id)
    logger.info("[SESSION] Jogo ID: %s.", game_id)
    if duration_minutes not in configured_durations(db, include_test_durations=True):
        logger.warning("[SESSION] Motivo da rejeição: duração %s minutos não configurada.", duration_minutes)
        raise ValueError("Duração de sessão indisponível.")
    if db.scalar(select(RacingSession.id).where(
            RacingSession.simulator_id == simulator_id,
            RacingSession.status.in_(["waiting", "running"]))):
        logger.warning("[SESSION] Motivo da rejeição: simulador ID %s já possui sessão ativa.", simulator_id)
        raise RuntimeError("O simulador já possui uma sessão ativa.")

    logger.info("[SESSION] Verificando disponibilidade do simulador.")
    simulator = db.get(Simulator, simulator_id)
    if not simulator:
        logger.warning("[SESSION] Motivo da rejeição: simulador ID %s não cadastrado.", simulator_id)
        raise ValueError("Simulador ou jogo indisponível.")
    logger.info("[SESSION] Simulador encontrado; status cadastrado: %s.", simulator.status)

    logger.info("[SESSION] Verificando disponibilidade do jogo.")
    game = db.get(Game, game_id)
    if not game:
        logger.warning("[SESSION] Motivo da rejeição: jogo ID %s não cadastrado.", game_id)
        raise ValueError("Simulador ou jogo indisponível.")
    if not game.is_enabled:
        logger.warning("[SESSION] Motivo da rejeição: jogo ID %s está desativado.", game_id)
        raise ValueError("Simulador ou jogo indisponível.")
    if game.name != ASSETTO_CORSA_COMPETIZIONE.name:
        logger.warning(
            "[SESSION] Motivo da rejeição: nome do jogo ID %s incompatível (cadastrado=%r; esperado=%r).",
            game_id, game.name, ASSETTO_CORSA_COMPETIZIONE.name,
        )
        raise ValueError("Simulador ou jogo indisponível.")

    logger.info("[SESSION] Verificando integração Steam.")
    if steam.find_game_processes():
        logger.warning("[SESSION] Motivo da rejeição: ACC já está em execução.")
        raise RuntimeError(f"{ASSETTO_CORSA_COMPETIZIONE.name} já está em execução; não é possível iniciar outra sessão.")
    now = datetime.now(timezone.utc)
    session = RacingSession(public_id=str(uuid4()), simulator_id=simulator_id,
        game_id=game_id, operator_id=user.id, status="waiting",
        created_at=now, guidance_started_at=now, contracted_minutes=duration_minutes)
    db.add(session)
    _audit(db, user.id, "session.start_requested", session, f"duration={duration_minutes}m")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RuntimeError("O simulador já possui uma sessão ativa.") from exc
    return session


def cancel_waiting_session(db: Session, public_id: str, user: User, steam) -> RacingSession:
    session = db.scalar(select(RacingSession).where(RacingSession.public_id == public_id))
    if not session:
        raise LookupError("Sessão não encontrada.")
    if session.status != "waiting":
        raise RuntimeError("Somente uma sessão aguardando pode ser cancelada.")
    if session.launch_requested_at:
        steam.close_game()
    session.status = "cancelled"
    session.ended_at = datetime.now(timezone.utc)
    session.effective_seconds = 0
    session.failure_reason = "Cancelada pelo operador antes do início da utilização."
    _audit(db, user.id, "session.cancelled", session)
    db.commit()
    return session


def end_running_session(db: Session, public_id: str, user: User, steam) -> RacingSession:
    session = db.scalar(select(RacingSession).where(RacingSession.public_id == public_id))
    if not session:
        raise LookupError("Sessão não encontrada.")
    if session.status != "running":
        raise RuntimeError("A sessão não está em andamento.")
    end_requested = datetime.now(timezone.utc)
    session.end_requested_at = end_requested
    steam.close_game()
    if steam.find_game_processes():
        raise RuntimeError("O jogo não encerrou; verifique o computador do simulador.")
    now = datetime.now(timezone.utc)
    started = session.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    session.ended_at = now
    session.effective_seconds = max(0, int((end_requested - started).total_seconds()))
    session.status = "completed"
    _audit(db, user.id, "session.ended_by_operator", session)
    db.commit()
    return session
