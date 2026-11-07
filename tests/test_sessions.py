def test_session_requires_authentication(client):
    response = client.post("/api/sessions", json={"price_option_id": 1})
    assert response.status_code == 401


def test_session_conflict_prevents_double_booking(client, db_session):
    # Admins can create operators. This test validates login and reservation flow.
    from app.models import User
    from app.services.auth import hash_password
    db = db_session
    db.add(User(username="op", password_hash=hash_password("long-enough-password"), role="operator"))
    db.commit()
    client.post("/api/auth/login", json={"username":"op", "password":"long-enough-password"})
    first = client.post("/api/sessions", json={"price_option_id": 1, "simulator_id": 1})
    second = client.post("/api/sessions", json={"price_option_id": 1, "simulator_id": 1})
    assert first.status_code == 201
    assert second.status_code == 409
