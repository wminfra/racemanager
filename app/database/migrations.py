from datetime import datetime, timezone

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Game, RacingSession


def migrate_legacy_sessions(db: Session) -> int:
    """Copy first-version session rows to the non-financial session table idempotently."""
    bind = db.get_bind()
    if "racing_sessions" not in inspect(bind).get_table_names():
        return 0
    available = {column["name"] for column in inspect(bind).get_columns("racing_sessions")}
    requested = ("public_id", "simulator_id", "game_id", "operator_id", "status", "started_at",
                 "ended_at", "contracted_minutes", "effective_seconds", "failure_reason")
    columns = [name for name in requested if name in available]
    if not {"public_id", "simulator_id", "operator_id", "status", "started_at", "contracted_minutes"}.issubset(columns):
        return 0
    rows = db.connection().exec_driver_sql(
        "SELECT " + ", ".join(columns) + " FROM racing_sessions").mappings().all()
    default_game = db.scalar(select(Game).order_by(Game.id))
    migrated = 0
    for row in rows:
        public_id = row.get("public_id")
        if not public_id or db.scalar(select(RacingSession.id).where(RacingSession.public_id == public_id)):
            continue
        status = row.get("status")
        if status not in {"completed", "cancelled", "interrupted", "technical_failure"}:
            status = "interrupted" if status == "running" else "technical_failure"
        ended_at = _datetime(row.get("ended_at"))
        started_at = _datetime(row.get("started_at"))
        effective_seconds = row.get("effective_seconds")
        failure = row.get("failure_reason")
        if row.get("status") == "running":
            ended_at = datetime.now(timezone.utc)
            failure = "Sessão ativa da versão anterior encerrada durante a migração."
            if started_at:
                started = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
                effective_seconds = max(0, int((ended_at - started).total_seconds()))
        game_id = row.get("game_id") or (default_game.id if default_game else None)
        if not game_id:
            continue
        db.add(RacingSession(
            public_id=public_id, simulator_id=row["simulator_id"], game_id=game_id,
            operator_id=row["operator_id"], status=status, created_at=started_at or datetime.now(timezone.utc),
            started_at=started_at, ended_at=ended_at, contracted_minutes=row["contracted_minutes"],
            effective_seconds=effective_seconds, failure_reason=failure,
        ))
        migrated += 1
    db.commit()
    return migrated


def migrate_session_operational_columns(db: Session) -> list[str]:
    """Add nullable operational timestamps without rebuilding or dropping session history."""
    bind = db.get_bind()
    if "sessions" not in inspect(bind).get_table_names():
        return []
    available = {column["name"] for column in inspect(bind).get_columns("sessions")}
    additions = {
        "guidance_started_at": "DATETIME",
        "launch_requested_at": "DATETIME",
        "end_requested_at": "DATETIME",
        "interrupted_at": "DATETIME",
        "interrupted_by": "INTEGER REFERENCES users(id)",
        "interruption_reason": "VARCHAR(80)",
        "interruption_note": "TEXT",
    }
    added = []
    with bind.begin() as connection:
        for name, sql_type in additions.items():
            if name not in available:
                connection.exec_driver_sql(f"ALTER TABLE sessions ADD COLUMN {name} {sql_type}")
                added.append(name)
    return added


def _datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def recover_open_sessions(db: Session, steam) -> int:
    """Never resume an open session after restart; close only the known game and mark it."""
    active = db.scalars(select(RacingSession).where(RacingSession.status.in_(["waiting", "running"]))).all()
    if not active:
        return 0
    should_close_game = any(session.status == "running" or session.launch_requested_at is not None
                            for session in active)
    close_failed = False
    if should_close_game:
        try:
            steam.close_game()
            if steam.find_game_processes():
                close_failed = True
        except Exception:
            close_failed = True
    now = datetime.now(timezone.utc)
    for session in active:
        if session.status == "running" and session.started_at:
            started = session.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if session.interrupted_at:
                interrupted_at = session.interrupted_at
                if interrupted_at.tzinfo is None:
                    interrupted_at = interrupted_at.replace(tzinfo=timezone.utc)
                session.effective_seconds = max(0, int((interrupted_at - started).total_seconds()))
                session.status = "technical_failure" if close_failed else "interrupted"
                session.failure_reason = (
                    "Falha t\u00e9cnica ao confirmar o encerramento do ACC ap\u00f3s rein\u00edcio do servi\u00e7o."
                    if close_failed else None
                )
            else:
                session.effective_seconds = max(0, int((now - started).total_seconds()))
                session.status = "interrupted"
                session.failure_reason = "Computador ou servi\u00e7o reiniciado durante a sess\u00e3o."
        else:
            session.status = "technical_failure"
            session.failure_reason = "Serviço reiniciado antes de confirmar o início do jogo."
        session.ended_at = now
        if close_failed:
            session.failure_reason = (session.failure_reason or "") + " Falha ao solicitar o fechamento do jogo."
        db.add(AuditLog(actor_id=session.operator_id, action="session.recovered_after_restart",
                        details=f"session={session.public_id} state={session.status}"))
    db.commit()
    return len(active)
