from app.models import User
from app.services.auth import hash_password
from httpx import URL


def test_unknown_user_cannot_login(client):
    response = client.post("/api/auth/login", json={"username": "missing", "password": "not-a-password"})
    assert response.status_code == 401


def test_operator_cannot_access_admin(client, db_session):
    db_session.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    assert client.post("/api/auth/login", json={"username": "racer", "password": "long-enough-password"}).status_code == 200
    assert client.get("/api/admin/summary").status_code == 403
    assert client.get("/api/admin/session-durations").status_code == 403


def test_bad_credentials_return_a_clear_error(client, db_session):
    db_session.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    response = client.post("/api/auth/login", json={"username": "racer", "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Usuário ou senha inválidos"
    assert "racing_manager_session" not in response.headers.get("set-cookie", "")


def test_operator_login_cookie_persists_on_localhost_and_ip_and_logout_clears_it(client, db_session):
    db_session.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    for origin in ("http://localhost:8000", "http://192.168.1.50:8000"):
        client.base_url = URL(origin)
        response = client.post("/api/auth/login", json={"username": "racer", "password": "long-enough-password"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "path=/" in cookie
        assert "secure" not in cookie
        assert client.get("/api/auth/me").json()["username"] == "racer"
        assert client.get("/operator").status_code == 200
        assert client.get("/api/operator/status").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
