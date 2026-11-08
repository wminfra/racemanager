"""Opt-in test that launches and closes the real ACC installation via Steam."""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import URL

from app.core.config import settings
from app.database.session import Base
from app.main import app
from app.models import User
from app.modules.steam.integration import SteamIntegration
from app.services.auth import hash_password


@pytest.mark.real_integration
@pytest.mark.skipif(os.environ.get("RACING_RUN_REAL_ACC_TESTS") != "1",
                    reason="Defina RACING_RUN_REAL_ACC_TESTS=1 para iniciar o ACC real")
def test_real_acc_starts_runs_for_two_minutes_and_closes(tmp_path):
    database_url = URL.create("sqlite", database=str(tmp_path / "acc-live-test.sqlite3"))
    engine = create_engine(database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    steam = SteamIntegration(settings)
    previous = (app.state.engine, app.state.session_factory, app.state.steam)
    previous_short_test_mode = settings.short_session_test_mode
    app.state.engine, app.state.session_factory, app.state.steam = engine, factory, steam
    settings.short_session_test_mode = True
    launched_by_test = False
    try:
        with TestClient(app) as client:
            readiness = steam.inspect()
            assert readiness["ready"], readiness["reason"]
            assert readiness["app_id"] == 805550
            assert readiness["game_installed"] and readiness["installation_accessible"]
            assert steam.find_game_processes() == [], "ACC já está aberto; o teste não vai encerrar um processo existente."
            username = "live-test-" + uuid.uuid4().hex[:8]
            password = uuid.uuid4().hex + "Aa9!"
            with factory() as db:
                db.add(User(username=username, password_hash=hash_password(password), role="operator"))
                db.commit()
            response = client.post("/api/auth/login", json={"username": username, "password": password})
            assert response.status_code == 200
            durations = client.get("/api/sessions/durations").json()["durations"]
            assert 1 in durations and 2 in durations
            started = client.post("/api/sessions", json={"duration_minutes": 2, "simulator_id": 1, "game_id": 1, "launch_mode": "monitor"})
            assert started.status_code == 201, started.text
            launched_by_test = True
            public_id = started.json()["id"]
            deadline = time.monotonic() + 330
            saw_running = False
            final_session = None
            while time.monotonic() < deadline:
                rows = client.get("/api/operator/sessions?limit=10").json()
                final_session = next((item for item in rows if item["id"] == public_id), None)
                if final_session and final_session["status"] == "running":
                    saw_running = True
                    assert client.get("/api/kiosk/status").json()["session"]["id"] == public_id
                    operator_status = client.get("/api/operator/status").json()
                    assert operator_status["session"]["id"] == public_id
                    assert operator_status["session"]["status"] == "running"
                if final_session and final_session["status"] in {"completed", "technical_failure", "interrupted"}:
                    break
                time.sleep(1)
            assert saw_running, f"ACC não foi detectado; estado final: {final_session}"
            assert final_session and final_session["status"] == "completed", final_session
            assert final_session["effective_seconds"] == 120
            assert steam.find_game_processes() == []
            assert steam.inspect()["steam_running"] is True, "O encerramento do ACC nao pode encerrar a Steam."
            # The Kiosk intentionally keeps its end screen visible briefly after
            # the backend has completed the session and closed the game.
            kiosk = client.get("/api/kiosk/status").json()
            kiosk_deadline = time.monotonic() + settings.session_end_display_seconds + 3
            while kiosk["session"] is not None and time.monotonic() < kiosk_deadline:
                time.sleep(0.25)
                kiosk = client.get("/api/kiosk/status").json()
            assert kiosk["session"] is None
    finally:
        try:
            if launched_by_test and steam.find_game_processes():
                steam.close_game(origin="real_integration_cleanup", retry=True)
        finally:
            app.state.engine, app.state.session_factory, app.state.steam = previous
            settings.short_session_test_mode = previous_short_test_mode
            Base.metadata.drop_all(engine)
            engine.dispose()
