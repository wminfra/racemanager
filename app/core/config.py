from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RACING_", env_file=".env", extra="ignore")

    secret_key: str = ""
    database_url: str = "sqlite:///./data/racing_manager.db"
    cookie_secure: bool = False
    session_cookie: str = "racing_manager_session"
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    steam_path: str = ""
    steam_library_path: str = ""
    assetto_corsa_path: str = ""
    short_session_test_mode: bool = False
    guidance_duration_seconds: int = 15
    session_end_display_seconds: int = 4
    session_failure_display_seconds: int = 8
    overlay_backend_url: str = "http://127.0.0.1:8000"


settings = Settings()
