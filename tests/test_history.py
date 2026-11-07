import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Game, RacingSession, User
from app.services.auth import hash_password


def admin_login(client, db_session):
    db_session.add(User(username="history-admin", password_hash=hash_password("long-enough-password"), role="admin"))
    db_session.commit()
    return client.post("/api/auth/login", json={
        "username": "history-admin", "password": "long-enough-password",
    })


def seed_history(db_session):
    operator = User(username="history-operator", password_hash=hash_password("long-enough-password"), role="operator")
    db_session.add(operator)
    db_session.flush()
    game = db_session.scalar(select(Game).where(Game.id == 1))
    rows = [
        RacingSession(public_id="hist-completed", simulator_id=1, game_id=game.id, operator_id=operator.id,
            status="completed", created_at=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
            started_at=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
            end_requested_at=datetime(2026, 11, 1, 10, 15, tzinfo=timezone.utc),
            ended_at=datetime(2026, 11, 1, 10, 15, 2, tzinfo=timezone.utc),
            contracted_minutes=15, effective_seconds=900),
        RacingSession(public_id="hist-cancelled", simulator_id=1, game_id=game.id, operator_id=operator.id,
            status="cancelled", created_at=datetime(2026, 11, 2, 11, tzinfo=timezone.utc),
            guidance_started_at=datetime(2026, 11, 2, 11, tzinfo=timezone.utc),
            ended_at=datetime(2026, 11, 2, 11, 0, 3, tzinfo=timezone.utc),
            contracted_minutes=15, effective_seconds=0, failure_reason="Cancelada pelo operador."),
        RacingSession(public_id="hist-interrupted", simulator_id=1, game_id=game.id, operator_id=operator.id,
            status="interrupted", created_at=datetime(2026, 11, 3, 12, tzinfo=timezone.utc),
            started_at=datetime(2026, 11, 3, 12, tzinfo=timezone.utc),
            interrupted_at=datetime(2026, 11, 3, 12, 1, tzinfo=timezone.utc), interrupted_by=operator.id,
            interruption_reason="Problema no volante", interruption_note="Volante perdeu o centro",
            ended_at=datetime(2026, 11, 3, 12, 1, tzinfo=timezone.utc),
            contracted_minutes=15, effective_seconds=60, failure_reason="Jogo fechado inesperadamente."),
        RacingSession(public_id="hist-failed", simulator_id=1, game_id=game.id, operator_id=operator.id,
            status="technical_failure", created_at=datetime(2026, 11, 4, 13, tzinfo=timezone.utc),
            guidance_started_at=datetime(2026, 11, 4, 13, tzinfo=timezone.utc),
            launch_requested_at=datetime(2026, 11, 4, 13, 0, 15, tzinfo=timezone.utc),
            ended_at=datetime(2026, 11, 4, 13, 0, 17, tzinfo=timezone.utc),
            contracted_minutes=15, effective_seconds=0, failure_reason="Steam indisponível."),
    ]
    db_session.add_all(rows)
    db_session.commit()


def test_admin_history_records_all_terminal_states_and_effective_play_time(client, db_session):
    assert admin_login(client, db_session).status_code == 200
    seed_history(db_session)
    response = client.get("/api/admin/history")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {
        "total": 4, "completed": 1, "cancelled": 1, "interrupted": 1,
        "technical_failure": 1, "effective_seconds": 960,
    }
    completed = next(item for item in data["items"] if item["session_id"] == "hist-completed")
    assert completed["operator"] == "history-operator"
    assert completed["game"] == "Assetto Corsa Competizione"
    assert completed["simulator"] == "Simulador 1"
    assert completed["effective_seconds"] == 900
    failed = next(item for item in data["items"] if item["session_id"] == "hist-failed")
    assert failed["started_at"] is None
    assert failed["effective_seconds"] == 0
    interrupted = next(item for item in data["items"] if item["session_id"] == "hist-interrupted")
    assert interrupted["interruption_reason"] == "Problema no volante"
    assert interrupted["interruption_note"] == "Volante perdeu o centro"
    assert interrupted["unused_seconds"] == 15 * 60 - 60
    assert "finance" not in str(data).lower()


def test_admin_history_filters_and_csv_export_share_the_selected_filters(client, db_session):
    assert admin_login(client, db_session).status_code == 200
    seed_history(db_session)
    operator_id = db_session.scalar(select(User.id).where(User.username == "history-operator"))
    response = client.get("/api/admin/history", params={
        "date_from": "2026-11-01", "date_to": "2026-11-01",
        "operator_id": operator_id, "game_id": 1, "status": "completed",
    })
    assert response.status_code == 200
    assert [item["session_id"] for item in response.json()["items"]] == ["hist-completed"]
    assert response.json()["summary"]["effective_seconds"] == 900

    exported = client.get("/api/admin/history.csv", params={"status": "completed"})
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.content.startswith(b"\xef\xbb\xbf")
    table = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert table[0][0:3] == ["ID", "Sessão", "Data"]
    assert any(row[1] == "hist-completed" and row[13] == "900" for row in table[1:])
    assert all("finance" not in cell.lower() and "valor" not in cell.lower() for row in table for cell in row)

    filtered = client.get("/api/admin/history", params={"interruption_reason": "Problema no volante"})
    assert filtered.status_code == 200
    assert [item["session_id"] for item in filtered.json()["items"]] == ["hist-interrupted"]
    interrupt_csv = client.get("/api/admin/history.csv", params={"interruption_reason": "Problema no volante"})
    assert interrupt_csv.status_code == 200
    assert "Problema no volante" in interrupt_csv.content.decode("utf-8-sig")


def test_history_endpoints_remain_admin_only(client, db_session):
    db_session.add(User(username="history-operator", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    assert client.post("/api/auth/login", json={
        "username": "history-operator", "password": "long-enough-password",
    }).status_code == 200
    assert client.get("/api/admin/history").status_code == 403
    assert client.get("/api/admin/history.csv").status_code == 403


def test_history_rejects_invalid_date_range(client, db_session):
    assert admin_login(client, db_session).status_code == 200
    response = client.get("/api/admin/history", params={
        "date_from": "2026-11-02", "date_to": "2026-11-01",
    })
    assert response.status_code == 422
