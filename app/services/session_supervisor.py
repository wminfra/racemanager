import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import AuditLog, RacingSession, Simulator

logger = logging.getLogger(__name__)


class SessionSupervisor:
    def __init__(self, session_factory, steam, launch_timeout_seconds: int = 120):
        self.session_factory = session_factory
        self.steam = steam
        self.launch_timeout_seconds = launch_timeout_seconds

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("Session supervisor tick failed")
            await asyncio.sleep(1)

    def tick(self):
        now = datetime.now(timezone.utc)
        with self.session_factory() as db:
            sessions = db.scalars(select(RacingSession).where(
                RacingSession.status.in_(["waiting", "running"]))).all()
            if not sessions:
                return
            for session in sessions:
                simulator = db.get(Simulator, session.simulator_id)
                processes = self.steam.find_game_processes()
                if session.status == "waiting":
                    created = session.created_at or now
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if processes:
                        session.started_at = now
                        session.status = "running"
                        if simulator:
                            simulator.status = "game_running"
                        db.add(AuditLog(actor_id=session.operator_id, action="session.game_started",
                            details=f"session={session.public_id}"))
                    elif (now - created).total_seconds() >= self.launch_timeout_seconds:
                        self._fail(db, session, now, "Assetto Corsa não iniciou dentro do tempo limite.", "session.launch_timeout")
                    continue

                started = session.started_at or now
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                remaining = session.contracted_minutes * 60 - int((now - started).total_seconds())
                if remaining <= 0:
                    try:
                        self.steam.close_game()
                    except Exception as exc:
                        self._fail(db, session, now, f"Falha ao encerrar o jogo: {exc}", "session.shutdown_failed")
                        continue
                    if self.steam.find_game_processes():
                        self._fail(db, session, now, "O jogo permaneceu ativo após o encerramento controlado.", "session.shutdown_failed")
                    else:
                        session.status = "completed"
                        session.ended_at = started.replace(microsecond=0) + timedelta(minutes=session.contracted_minutes)
                        session.effective_seconds = session.contracted_minutes * 60
                        db.add(AuditLog(actor_id=session.operator_id, action="session.completed_by_timer",
                            details=f"session={session.public_id}"))
                    if simulator:
                        simulator.status = "ready"
                    continue

                if remaining <= 120 and not session.warning_2m_sent:
                    session.warning_2m_sent = True
                    db.add(AuditLog(actor_id=session.operator_id, action="session.warning_2m",
                        details=f"session={session.public_id}"))
                if remaining <= 30 and not session.warning_30s_sent:
                    session.warning_30s_sent = True
                    db.add(AuditLog(actor_id=session.operator_id, action="session.warning_30s",
                        details=f"session={session.public_id}"))
                if not processes:
                    self._fail(db, session, now, "Assetto Corsa encerrou antes do fim da sessão.", "session.game_exited_unexpectedly", interrupted=True)
                elif self.steam.is_game_unresponsive(processes):
                    if simulator:
                        simulator.status = "game_unresponsive"
                    try:
                        self.steam.close_game()
                    except Exception:
                        logger.exception("Failed to close unresponsive Assetto Corsa process")
                    self._fail(db, session, now, "O jogo parou de responder.", "session.game_unresponsive")
                elif simulator:
                    simulator.status = "game_running"
            db.commit()

    @staticmethod
    def _fail(db, session, now, reason, action, interrupted=False):
        started = session.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        session.status = "interrupted" if interrupted else "technical_failure"
        session.ended_at = now
        session.effective_seconds = max(0, int((now - started).total_seconds())) if started else 0
        session.failure_reason = reason[:1000]
        db.add(AuditLog(actor_id=session.operator_id, action=action,
            details=f"session={session.public_id} {reason}"[:2000]))
