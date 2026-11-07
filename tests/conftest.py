import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("RACING_SECRET_KEY", "test-only-secret-key-that-is-long-enough")

from app.database.session import Base, get_db
from app.main import app


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    app.state.testing_session = TestingSession
    def override_db():
        with TestingSession() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        with TestingSession() as db:
            from app.models import Game, PriceOption, Simulator
            db.add_all([Simulator(name="Test Rig"), Game(name="Test Game"), PriceOption(name="15 min", duration_minutes=15, price=25, venue_share_percent=60)])
            db.commit()
        yield c
    app.dependency_overrides.clear()
    del app.state.testing_session
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(client):
    with app.state.testing_session() as db:
        yield db
