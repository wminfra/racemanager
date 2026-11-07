from app.models import User
from app.services.auth import hash_password


def test_unknown_user_cannot_login(client):
    response = client.post("/api/auth/login", json={"username": "missing", "password": "not-a-password"})
    assert response.status_code == 401


def test_operator_cannot_access_admin(client, db_session):
    db_session.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    assert client.post("/api/auth/login", json={"username": "racer", "password": "long-enough-password"}).status_code == 200
    assert client.get("/api/admin/summary").status_code == 403
    assert client.get("/api/admin/session-durations").status_code == 403
