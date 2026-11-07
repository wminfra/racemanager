from app.models import User
from app.services.auth import hash_password
from app.database.session import SessionLocal


def test_unknown_user_cannot_login(client):
    response = client.post("/api/auth/login", json={"username": "missing", "password": "not-a-password"})
    assert response.status_code == 401


def test_operator_cannot_access_admin(client, db_session):
    db = db_session
    db.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db.commit()
    assert client.post("/api/auth/login", json={"username":"racer", "password":"long-enough-password"}).status_code == 200
    assert client.get("/api/admin/summary").status_code == 403
