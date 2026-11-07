from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database.migrations import migrate_legacy_sessions, recover_open_sessions
from app.core.config import settings
from app.models import Game, RacingSession, Simulator, User
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.services.auth import hash_password
from app.services.session_supervisor import SessionSupervisor


def operator_login(client, db_session):
    db_session.add(User(username="operator", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    return client.post("/api/auth/login", json={"username": "operator", "password": "long-enough-password"})


def create_session(client, db_session, duration=15):
    operator_login(client, db_session)
    return client.post("/api/sessions", json={"duration_minutes": duration, "simulator_id": 1, "game_id": 1})


def test_session_starts_through_steam_adapter(client, db_session):
    response = create_session(client, db_session)
    assert response.status_code == 201
    assert response.json()["status"] == "waiting"
    assert client.steam.launch_calls == 1
    assert "gross_amount" not in response.json()


def test_two_simultaneous_sessions_are_prevented(client, db_session):
    assert create_session(client, db_session).status_code == 201
    second = client.post("/api/sessions", json={"duration_minutes": 15, "simulator_id": 1, "game_id": 1})
    assert second.status_code == 409
    assert client.steam.launch_calls == 1


def test_session_cannot_start_when_steam_is_not_ready(client, db_session):
    operator_login(client, db_session)
    client.steam.ready = False
    response = client.post("/api/sessions", json={"duration_minutes": 15, "simulator_id": 1, "game_id": 1})
    assert response.status_code == 409
    assert client.steam.launch_calls == 0


def test_one_and_two_minute_test_options_are_temporary_and_not_admin_packages(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "short_session_test_mode", True)
    db_session.add(User(username="administrator", password_hash=hash_password("long-enough-password"), role="admin"))
    db_session.commit()
    assert client.post("/api/auth/login", json={"username":"administrator", "password":"long-enough-password"}).status_code == 200
    assert client.get("/api/sessions/durations").json()["durations"] == [1, 2, 15, 30, 60]
    assert client.get("/api/admin/session-durations").json()["durations"] == [15, 30, 60]
    assert client.put("/api/admin/session-durations", json={"durations":[1, 2]}).status_code == 422
    assert client.get("/api/admin/session-durations").json()["durations"] == [15, 30, 60]


def test_launch_failure_is_recorded(client, db_session):
    operator_login(client, db_session)
    client.steam.launch_error = "simulated launch failure"
    response = client.post("/api/sessions", json={"duration_minutes": 15, "simulator_id": 1, "game_id": 1})
    assert response.status_code == 409
    row = db_session.scalar(select(RacingSession))
    assert row.status == "technical_failure"
    assert "simulated launch failure" in row.failure_reason


def test_waiting_session_can_be_cancelled(client, db_session):
    client.steam.auto_start = False
    response = create_session(client, db_session)
    assert response.status_code == 201
    result = client.post(f"/api/sessions/{response.json()['id']}/cancel")
    assert result.status_code == 200
    assert result.json()["status"] == "cancelled"


def test_operator_can_end_a_running_session_through_controlled_adapter(client, db_session):
    response = create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    result = client.post(f"/api/sessions/{response.json()['id']}/end")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["effective_seconds"] >= 60
    assert client.steam.close_calls == 1


def test_timer_completes_once_and_closes_only_the_adapter_process(client, db_session):
    response = create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db_session.commit()
    supervisor = SessionSupervisor(client.session_factory, client.steam)
    supervisor.tick()
    supervisor.tick()
    db_session.refresh(row)
    assert row.status == "completed"
    assert row.effective_seconds == 15 * 60
    assert client.steam.close_calls == 1
    assert db_session.scalar(select(func.count(RacingSession.id))) == 1


def test_backend_sets_two_minute_and_thirty_second_warning_states(client, db_session):
    create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=13)
    db_session.commit()
    supervisor = SessionSupervisor(client.session_factory, client.steam)
    supervisor.tick()
    db_session.refresh(row)
    assert row.warning_2m_sent is True
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=14, seconds=31)
    db_session.commit()
    supervisor.tick()
    db_session.refresh(row)
    assert row.warning_30s_sent is True


def test_unexpected_game_exit_interrupts_session(client, db_session):
    response = create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    db_session.commit()
    client.steam.running = False
    SessionSupervisor(client.session_factory, client.steam).tick()
    db_session.refresh(row)
    assert row.status == "interrupted"
    assert row.effective_seconds >= 20


def test_unresponsive_game_is_a_technical_failure(client, db_session):
    create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()
    client.steam.unresponsive = True
    SessionSupervisor(client.session_factory, client.steam).tick()
    db_session.refresh(row)
    assert row.status == "technical_failure"
    assert "responder" in row.failure_reason


def test_restart_marks_open_session_interrupted_and_does_not_resume(client, db_session):
    operator_login(client, db_session)
    second_simulator = Simulator(name="Test Rig 2")
    db_session.add(second_simulator)
    db_session.flush()
    game = db_session.scalar(select(Game).where(Game.name == ASSETTO_CORSA_COMPETIZIONE.name))
    db_session.add_all([
        RacingSession(public_id="recover-running", simulator_id=1, game_id=game.id, operator_id=1,
            status="running", started_at=datetime.now(timezone.utc)-timedelta(seconds=50), contracted_minutes=15),
        RacingSession(public_id="recover-waiting", simulator_id=second_simulator.id, game_id=game.id, operator_id=1,
            status="waiting", contracted_minutes=15),
    ])
    db_session.commit()
    client.steam.running = True
    assert recover_open_sessions(db_session, client.steam) == 2
    rows = db_session.scalars(select(RacingSession).order_by(RacingSession.public_id)).all()
    assert [row.status for row in rows] == ["interrupted", "technical_failure"]
    assert client.steam.running is False


def test_legacy_session_migration_preserves_history_without_money_and_is_idempotent(client, db_session):
    operator_login(client, db_session)
    started = (datetime.now(timezone.utc)-timedelta(minutes=4)).isoformat()
    db_session.connection().exec_driver_sql(
        "CREATE TABLE racing_sessions (public_id TEXT, simulator_id INTEGER, game_id INTEGER, "
        "operator_id INTEGER, status TEXT, started_at TEXT, ended_at TEXT, contracted_minutes INTEGER, "
        "effective_seconds INTEGER, failure_reason TEXT)"
    )
    db_session.connection().exec_driver_sql(
        "INSERT INTO racing_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy-1", 1, 1, 1, "completed", started, datetime.now(timezone.utc).isoformat(), 15, 240, None),
    )
    db_session.commit()
    assert migrate_legacy_sessions(db_session) == 1
    assert migrate_legacy_sessions(db_session) == 0
    row = db_session.scalar(select(RacingSession).where(RacingSession.public_id == "legacy-1"))
    assert row.status == "completed"
    assert row.effective_seconds == 240
    assert not hasattr(row, "gross_amount")


def test_history_is_single_count_even_after_repeated_monitor_ticks(client, db_session):
    create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db_session.commit()
    supervisor = SessionSupervisor(client.session_factory, client.steam)
    supervisor.tick()
    supervisor.tick()
    response = client.get("/api/operator/sessions")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["status"] == "completed"


def test_public_kiosk_status_does_not_expose_local_paths(client):
    response = client.get("/api/kiosk/status")
    assert response.status_code == 200
    assert "steam" not in response.json()
    assert "game_path" not in response.text


def test_kiosk_receives_public_read_only_websocket_state(client):
    with client.websocket_connect("/api/ws/kiosk") as websocket:
        update = websocket.receive_json()
    assert update["type"] == "session_status"
    assert update["session"] is None
    assert "running_session" not in update
