from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayFrame:
    visible: bool
    text: str = ""
    remaining_seconds: int | None = None
    terminal: bool = False


def frame_from_kiosk_state(state: dict | None) -> OverlayFrame:
    """Convert the backend's public kiosk state into display-only overlay data."""
    if not isinstance(state, dict):
        return OverlayFrame(False)
    session = state.get("session")
    if state.get("screen") == "playing" and isinstance(session, dict):
        seconds = session.get("remaining_seconds")
        if not isinstance(seconds, int):
            return OverlayFrame(False)
        seconds = max(0, seconds)
        return OverlayFrame(True, f"{seconds // 60:02d}:{seconds % 60:02d}", seconds)
    if state.get("screen") == "ending":
        return OverlayFrame(True, "00:00", 0, True)
    return OverlayFrame(False)


def flash_interval_ms(remaining_seconds: int | None) -> int | None:
    if remaining_seconds is None or remaining_seconds > 30:
        return None
    return 180 if remaining_seconds <= 10 else 500
