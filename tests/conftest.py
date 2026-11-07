import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("RACING_SECRET_KEY", "test-only-secret-key-that-is-long-enough")

from app.database.session import Base
from app.main import app
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE


class FakeSteam:
    def __init__(self):
        self.ready = True
        self.running = False
        self.unresponsive = False
        self.launch_error = None
        self.auto_start = True
        self.launch_calls = 0
        self.close_calls = 0

    def inspect(self):
        return {"ready": self.ready, "steam_installed": self.ready, "game_installed": self.ready,
                "steam_running": self.ready, "account_selected": self.ready,
                "authentication": "test_ready" if self.ready else "not_ready",
                "app_id": ASSETTO_CORSA_COMPETIZIONE.app_id, "game_path": "simulated", "reason": "Simulação pronta" if self.ready else "Steam indisponível"}

    def find_game_processes(self):
        return [object()] if self.running else []

    def launch_game(self):
        self.launch_calls += 1
        if self.launch_error:
            raise RuntimeError(self.launch_error)
        if not self.ready:
            raise RuntimeError("Steam indisponível")
        self.running = self.auto_start

    def close_game(self, grace_seconds=8):
        self.close_calls += 1
        self.running = False

    def is_game_unresponsive(self, processes=None):
        return self.unresponsive and self.running


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    steam = FakeSteam()
    previous = (app.state.engine, app.state.session_factory, app.state.steam)
    app.state.engine, app.state.session_factory, app.state.steam = engine, TestingSession, steam
    with TestClient(app) as test_client:
        test_client.steam = steam
        test_client.session_factory = TestingSession
        yield test_client
    app.state.engine, app.state.session_factory, app.state.steam = previous
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(client):
    with client.session_factory() as db:
        yield db
