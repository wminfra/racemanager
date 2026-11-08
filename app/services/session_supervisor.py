import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import AuditLog, RacingSession, Simulator
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.modules.steam.shutdown import GameCloseError

logger = logging.getLogger(__name__)


class SessionSupervisor:
    def __init__(self, session_factory, steam, launch_timeout_seconds: int = 120,
                 unresponsive_grace_seconds: int = 60, guidance_duration_seconds: int = 15,
                 transition_lock=None):
        self.session_factory = session_factory
        self.steam = steam
        self.launch_timeout_seconds = launch_timeout_seconds
        self.unresponsive_grace_seconds = unresponsive_grace_seconds
        self.guidance_duration_seconds = max(0, guidance_duration_seconds)
        self.transition_lock = transition_lock or threading.RLock()

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("Session supervisor tick failed")
            await asyncio.sleep(1)

    def tick(self):
        with self.transition_lock:
            self._tick_locked()

    def _tick_locked(self):
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
                    if session.launch_requested_at is None:
                        guidance_started = session.guidance_started_at or session.created_at or now
                        if guidance_started.tzinfo is None:
                            guidance_started = guidance_started.replace(tzinfo=timezone.utc)
                        if processes:
                            self._fail(db, session, now,
                                "O ACC já estava em execução durante as orientações; a sessão não foi iniciada.",
                                "session.game_running_before_launch")
                        elif (now - guidance_started).total_seconds() >= self.guidance_duration_seconds:
                            session.launch_requested_at = now
                            db.commit()
                            try:
                                self.steam.launch_game(mode=session.launch_mode)
                                db.add(AuditLog(actor_id=session.operator_id, action="session.game_launch_requested",
                                    details=f"session={session.public_id}"))
                                db.commit()
                            except Exception as exc:
                                self._fail(db, session, datetime.now(timezone.utc),
                                    f"Falha ao iniciar a Steam/o jogo: {exc}", "session.launch_failed")
                                db.commit()
                        continue
                    if processes:
                        session.started_at = now
                        session.status = "running"
                        if simulator:
                            simulator.status = "game_running"
                        db.add(AuditLog(actor_id=session.operator_id, action="session.game_started",
                            details=f"session={session.public_id}"))
                    else:
                        launch_requested = session.launch_requested_at
                        if launch_requested.tzinfo is None:
                            launch_requested = launch_requested.replace(tzinfo=timezone.utc)
                        if (now - launch_requested).total_seconds() >= self.launch_timeout_seconds:
                            self._fail(db, session, now, f"{ASSETTO_CORSA_COMPETIZIONE.name} não iniciou dentro do tempo limite.", "session.launch_timeout")
                    continue

                started = session.started_at or now
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                remaining = session.contracted_minutes * 60 - int((now - started).total_seconds())
                if remaining <= 0:
                    end_requested = started + timedelta(minutes=session.contracted_minutes)
                    if session.end_requested_at is None:
                        session.end_requested_at = end_requested
                        db.commit()
                    shutdown = self.steam.close_status() if hasattr(self.steam, "close_status") else {}
                    if shutdown.get("state") == "CLOSE_FAILED":
                        if simulator:
                            simulator.status = "game_unresponsive"
                        continue
                    try:
                        self.steam.close_game(origin="session_timer")
                    except GameCloseError as exc:
                        session.failure_reason = str(exc)[:1000]
                        if simulator:
                            simulator.status = "game_unresponsive"
                        db.add(AuditLog(actor_id=session.operator_id, action="session.shutdown_failed",
                            details=f"session={session.public_id} state=CLOSE_FAILED"))
                        continue
                    except Exception as exc:
                        self._fail(db, session, now, f"Falha ao encerrar o jogo: {exc}", "session.shutdown_failed")
                        continue
                    if self.steam.find_game_processes():
                        session.failure_reason = "O ACC permaneceu ativo apos o fechamento normal; o simulador continua bloqueado."
                        if simulator:
                            simulator.status = "game_unresponsive"
                        db.add(AuditLog(actor_id=session.operator_id, action="session.shutdown_failed",
                            details=f"session={session.public_id} state=CLOSE_FAILED"))
                    else:
                        session.status = "completed"
                        session.ended_at = datetime.now(timezone.utc)
                        session.effective_seconds = max(0, int((end_requested - started).total_seconds()))
                        session.failure_reason = None
                        db.add(AuditLog(actor_id=session.operator_id, action="session.completed_by_timer",
                            details=f"session={session.public_id}"))
                    if simulator and session.status == "completed":
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
                    self._fail(db, session, now, f"{ASSETTO_CORSA_COMPETIZIONE.name} encerrou antes do fim da sessão.", "session.game_exited_unexpectedly", interrupted=True)
                elif ((now - started).total_seconds() >= self.unresponsive_grace_seconds
                      and self.steam.is_game_unresponsive(processes)):
                    if simulator:
                        simulator.status = "game_unresponsive"
                    if session.end_requested_at is None:
                        session.end_requested_at = now
                        db.commit()
                    shutdown = self.steam.close_status() if hasattr(self.steam, "close_status") else {}
                    if shutdown.get("state") == "CLOSE_FAILED":
                        continue
                    try:
                        self.steam.close_game(origin="unresponsive_monitor")
                    except GameCloseError as exc:
                        session.failure_reason = ("O Windows sinalizou uma janela do processo do ACC como não respondendo; "
                                                  "o WM_CLOSE não confirmou o encerramento: " + str(exc))[:1000]
                        db.add(AuditLog(actor_id=session.operator_id, action="session.shutdown_failed",
                            details=f"session={session.public_id} origin=unresponsive_monitor state=CLOSE_FAILED"))
                        continue
                    except Exception:
                        logger.exception("Failed to close unresponsive Assetto Corsa process")
                        continue
                    if self.steam.find_game_processes():
                        session.failure_reason = ("O Windows sinalizou uma janela do processo do ACC como não respondendo; "
                                                  "o jogo continua ativo após o pedido WM_CLOSE.")
                        if simulator:
                            simulator.status = "game_unresponsive"
                        db.add(AuditLog(actor_id=session.operator_id, action="session.shutdown_failed",
                            details=f"session={session.public_id} origin=unresponsive_monitor state=CLOSE_FAILED"))
                        continue
                    self._fail(db, session, now,
                               "O Windows sinalizou uma janela do processo do ACC como não respondendo; "
                               "o jogo encerrou após o pedido WM_CLOSE.", "session.game_unresponsive")
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
        logger.error("Session %s failed (%s): %s", session.public_id, action, reason)
        db.add(AuditLog(actor_id=session.operator_id, action=action,
            details=f"session={session.public_id} {reason}"[:2000]))
