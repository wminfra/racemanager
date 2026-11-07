from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.migrations import migrate_session_operational_columns


def test_session_timestamp_migration_adds_nullable_columns_without_rebuilding_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE sessions (id INTEGER PRIMARY KEY, public_id TEXT, status TEXT, contracted_minutes INTEGER)"
        )
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        connection.exec_driver_sql(
            "INSERT INTO sessions (id, public_id, status, contracted_minutes) VALUES (1, 'legacy-keep', 'completed', 15)"
        )
    with Session(engine) as db:
        added = migrate_session_operational_columns(db)
        assert added == ["guidance_started_at", "launch_requested_at", "end_requested_at", "interrupted_at", "interrupted_by", "interruption_reason", "interruption_note"]
        assert migrate_session_operational_columns(db) == []
        row = db.execute(text("SELECT id, public_id, status, contracted_minutes, guidance_started_at, interrupted_at, "
                              "interrupted_by, interruption_reason, interruption_note "
                              "FROM sessions WHERE id=1")).one()
        assert tuple(row) == (1, "legacy-keep", "completed", 15, None, None, None, None, None)
    engine.dispose()
