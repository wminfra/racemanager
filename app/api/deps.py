from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import User


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticação necessária")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    return user


def operator_or_admin(user: User = Depends(current_user)) -> User:
    if user.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Acesso restrito a operadores e administradores")
    return user
