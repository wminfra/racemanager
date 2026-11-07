from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, RacingSession, Simulator, User

HISTORY_STATUSES = ("waiting", "running", "completed", "cancelled", "interrupted", "technical_failure")


def _query(db: Session, date_from: date | None, date_to: date | None,
           operator_id: int | None, game_id: int | None, status: str | None):
    query = (select(RacingSession, User.username, Game.name, Simulator.name)
             .outerjoin(User, User.id == RacingSession.operator_id)
             .outerjoin(Game, Game.id == RacingSession.game_id)
             .outerjoin(Simulator, Simulator.id == RacingSession.simulator_id))
    session_day = func.date(func.coalesce(RacingSession.started_at, RacingSession.created_at))
    if date_from:
        query = query.where(session_day >= date_from.isoformat())
    if date_to:
        query = query.where(session_day <= date_to.isoformat())
    if operator_id:
        query = query.where(RacingSession.operator_id == operator_id)
    if game_id:
        query = query.where(RacingSession.game_id == game_id)
    if status:
        query = query.where(RacingSession.status == status)
    return query.order_by(RacingSession.created_at.desc(), RacingSession.id.desc())


def _iso(value):
    return value.isoformat() if value else None


def _record(session: RacingSession, operator_name: str | None,
            game_name: str | None, simulator_name: str | None) -> dict:
    event_date = session.started_at or session.created_at
    effective = session.effective_seconds if session.started_at else 0
    reason = session.failure_reason
    if session.status == "cancelled" and not reason:
        reason = "Motivo não registrado em versão anterior."
    return {
        "id": session.id,
        "session_id": session.public_id,
        "date": event_date.date().isoformat() if event_date else None,
        "requested_at": _iso(session.created_at),
        "guidance_started_at": _iso(session.guidance_started_at),
        "launch_requested_at": _iso(session.launch_requested_at),
        "started_at": _iso(session.started_at),
        "end_requested_at": _iso(session.end_requested_at),
        "ended_at": _iso(session.ended_at),
        "game": game_name or "Jogo removido",
        "simulator": simulator_name or "Simulador removido",
        "operator": operator_name or "Operador não identificado",
        "contracted_minutes": session.contracted_minutes,
        "effective_seconds": effective or 0,
        "status": session.status,
        "reason": reason,
    }


def read_history(db: Session, date_from: date | None = None, date_to: date | None = None,
                 operator_id: int | None = None, game_id: int | None = None,
                 status: str | None = None) -> dict:
    records = [_record(*row) for row in db.execute(
        _query(db, date_from, date_to, operator_id, game_id, status)).all()]
    summary = {
        "total": len(records),
        "completed": sum(item["status"] == "completed" for item in records),
        "cancelled": sum(item["status"] == "cancelled" for item in records),
        "interrupted": sum(item["status"] == "interrupted" for item in records),
        "technical_failure": sum(item["status"] == "technical_failure" for item in records),
        "effective_seconds": sum(item["effective_seconds"] for item in records),
    }
    operators = db.scalars(select(User).where(User.role == "operator").order_by(User.username)).all()
    games = db.scalars(select(Game).order_by(Game.name)).all()
    return {
        "summary": summary,
        "items": records,
        "filters": {
            "operators": [{"id": item.id, "username": item.username} for item in operators],
            "games": [{"id": item.id, "name": item.name} for item in games],
            "statuses": list(HISTORY_STATUSES),
        },
    }
