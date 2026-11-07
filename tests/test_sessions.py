from datetime import datetime, timedelta, timezone
import logging

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
    response = client.post("/api/sessions", json={"duration_minutes": duration, "simulator_id": 1, "game_id": 1})
    if response.status_code == 201:
        supervisor = SessionSupervisor(client.session_factory, client.steam, guidance_duration_seconds=0)
        supervisor.tick()
        supervisor.tick()
    return response


def test_session_starts_through_steam_adapter(client, db_session, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.sessions"):
        response = create_session(client, db_session)
    assert response.status_code == 201
    assert response.json()["status"] == "waiting"
    assert client.steam.launch_calls == 1
    assert "gross_amount" not in response.json()
    assert "[SESSION] Solicitação recebida." in caplog.text
    assert "[SESSION] Simulador ID: 1." in caplog.text
    assert "[SESSION] Jogo ID: 1." in caplog.text
    assert "[SESSION] Verificando integração Steam." in caplog.text


def test_session_does_not_launch_game_before_guidance_finishes(client, db_session):
    operator_login(client, db_session)
    response = client.post("/api/sessions", json={
        "duration_minutes": 15, "simulator_id": 1, "game_id": 1,
    })
    row = db_session.scalar(select(RacingSession))
    assert response.status_code == 201
    assert row.status == "waiting"
    assert row.guidance_started_at is not None
    assert row.launch_requested_at is None
    assert client.steam.launch_calls == 0


def test_cancel_during_guidance_never_launches_or_closes_a_game(client, db_session):
    operator_login(client, db_session)
    response = client.post("/api/sessions", json={
        "duration_minutes": 15, "simulator_id": 1, "game_id": 1,
    })
    result = client.post(f"/api/sessions/{response.json()['id']}/cancel")
    row = db_session.scalar(select(RacingSession))
    assert result.status_code == 200
    assert result.json()["status"] == "cancelled"
    assert row.failure_reason == "Cancelada pelo operador antes do início da utilização."
    assert client.steam.launch_calls == 0
    assert client.steam.close_calls == 0


def test_guidance_launch_records_failed_steam_attempt(client, db_session):
    operator_login(client, db_session)
    response = client.post("/api/sessions", json={
        "duration_minutes": 15, "simulator_id": 1, "game_id": 1,
    })
    supervisor = SessionSupervisor(client.session_factory, client.steam, guidance_duration_seconds=0)
    client.steam.ready = False
    supervisor.tick()
    row = db_session.scalar(select(RacingSession))
    assert response.status_code == 201
    assert row.status == "technical_failure"
    assert row.started_at is None
    assert row.effective_seconds == 0
    assert "Steam indisponível" in row.failure_reason
    assert client.steam.launch_calls == 1


def test_game_mismatch_returns_specific_session_rejection_log(client, db_session, caplog, monkeypatch):
    monkeypatch.setattr(settings, "short_session_test_mode", True)
    operator_login(client, db_session)
    game = db_session.get(Game, 1)
    game.name = "Assetto Corsa"
    db_session.commit()

    with caplog.at_level(logging.INFO, logger="app.services.sessions"):
        response = client.post("/api/sessions", json={
            "duration_minutes": 2, "simulator_id": 1, "game_id": 1,
        })

    assert response.status_code == 422
    assert response.json()["detail"] == "Simulador ou jogo indisponível."
    assert "[SESSION] Solicitação recebida." in caplog.text
    assert "[SESSION] Simulador ID: 1." in caplog.text
    assert "[SESSION] Jogo ID: 1." in caplog.text
    assert "[SESSION] Verificando disponibilidade do simulador." in caplog.text
    assert "[SESSION] Verificando disponibilidade do jogo." in caplog.text
    assert "cadastrado='Assetto Corsa'; esperado='Assetto Corsa Competizione'" in caplog.text
    assert "[SESSION] Motivo da rejeição:" in caplog.text
    assert "[SESSION] Verificando integração Steam." not in caplog.text


def test_two_simultaneous_sessions_are_prevented(client, db_session):
    assert create_session(client, db_session).status_code == 201
    second = client.post("/api/sessions", json={"duration_minutes": 15, "simulator_id": 1, "game_id": 1})
    assert second.status_code == 409
    assert client.steam.launch_calls == 1


def test_session_cannot_start_when_steam_is_not_ready(client, db_session):
    operator_login(client, db_session)
    client.steam.ready = False
    response = client.post("/api/sessions", json={"duration_minutes": 15, "simulator_id": 1, "game_id": 1})
    assert response.status_code == 201
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
    SessionSupervisor(client.session_factory, client.steam, guidance_duration_seconds=0).tick()
    assert response.status_code == 201
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
    row.started_at = datetime.now(timezone.utc) - timedelta(seconds=70)
    db_session.commit()
    client.steam.unresponsive = True
    SessionSupervisor(client.session_factory, client.steam).tick()
    db_session.refresh(row)
    assert row.status == "technical_failure"
    assert "responder" in row.failure_reason


def test_unresponsive_game_gets_startup_grace_period(client, db_session):
    create_session(client, db_session)
    row = db_session.scalar(select(RacingSession))
    row.status = "running"
    row.started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()
    client.steam.unresponsive = True

    SessionSupervisor(client.session_factory, client.steam).tick()

    db_session.refresh(row)
    assert row.status == "running"
    assert client.steam.close_calls == 0


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


def test_kiosk_initial_state_and_orientation_countdown_are_backend_driven(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "guidance_duration_seconds", 15)
    waiting = client.get("/api/kiosk/status").json()
    assert waiting["screen"] == "waiting"
    assert waiting["session"] is None
    operator_login(client, db_session)
    response = client.post("/api/sessions", json={
        "duration_minutes": 15, "simulator_id": 1, "game_id": 1,
    })
    guidance = client.get("/api/kiosk/status").json()
    assert response.status_code == 201
    assert guidance["screen"] == "guidance"
    assert guidance["session"]["phase"] == "guidance"
    assert 0 <= guidance["session"]["guidance_remaining_seconds"] <= 15
    assert len(guidance["guidance_messages"]) == 3
    assert client.steam.launch_calls == 0
    assert "operator_id" not in guidance["session"]
    assert "warning" not in guidance["session"]


def test_kiosk_is_silent_during_game_then_shows_end_screen(client, db_session):
    create_session(client, db_session)
    running = client.get("/api/kiosk/status").json()
    assert running["screen"] == "playing"
    assert running["session"]["status"] == "running"
    assert "warning" not in running["session"]
    row = db_session.scalar(select(RacingSession))
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db_session.commit()
    SessionSupervisor(client.session_factory, client.steam).tick()
    ended = client.get("/api/kiosk/status").json()
    assert ended["screen"] == "ending"
    assert ended["session"]["remaining_seconds"] == 0


def test_kiosk_shows_generic_failure_after_delayed_launch_failure(client, db_session):
    operator_login(client, db_session)
    client.steam.launch_error = "steam launch failed"
    response = client.post("/api/sessions", json={
        "duration_minutes": 15, "simulator_id": 1, "game_id": 1,
    })
    SessionSupervisor(client.session_factory, client.steam, guidance_duration_seconds=0).tick()
    failure = client.get("/api/kiosk/status").json()
    assert response.status_code == 201
    assert failure["screen"] == "failure"
    assert "failure_reason" not in failure["session"]
    assert client.steam.close_calls == 0
