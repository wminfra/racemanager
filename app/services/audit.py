from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, actor_id: int | None, action: str, details: str = "") -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, details=details[:2000]))
