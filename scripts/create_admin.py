import getpass

from sqlalchemy import select

from app.database.session import Base, SessionLocal, engine
from app.models import User
from app.services.auth import hash_password


def main():
    Base.metadata.create_all(engine)
    username = input("Novo usuário administrador: ").strip()
    password = getpass.getpass("Senha (mínimo 12 caracteres): ")
    if len(password) < 12:
        raise SystemExit("A senha deve ter pelo menos 12 caracteres.")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("Esse usuário já existe.")
        db.add(User(username=username, password_hash=hash_password(password), role="admin"))
        db.commit()
    print("Administrador criado.")


if __name__ == "__main__":
    main()
