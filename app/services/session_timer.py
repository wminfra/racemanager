import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models import RacingSession


async def expire_sessions() -> None:
    """Persist completion using server time, independently of the browser display."""
    while True:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            sessions = db.scalars(select(RacingSession).where(RacingSession.status == "running")).all()
            changed = False
            for session in sessions:
                started = session.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                deadline = started.timestamp() + session.contracted_minutes * 60
                if now.timestamp() >= deadline:
                    ended = datetime.fromtimestamp(deadline, tz=timezone.utc)
                    session.ended_at = ended
                    session.effective_seconds = session.contracted_minutes * 60
                    session.status = "completed"
                    changed = True
            if changed:
                db.commit()
        await asyncio.sleep(1)
