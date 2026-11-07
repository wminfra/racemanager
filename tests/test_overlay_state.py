import asyncio

from app.modules.overlay.feed import receive_kiosk_states
from app.modules.overlay.state import flash_interval_ms, frame_from_kiosk_state


def test_overlay_stays_hidden_without_a_running_session():
    assert frame_from_kiosk_state({"screen": "waiting", "session": None}).visible is False
    assert frame_from_kiosk_state({"screen": "guidance", "session": {"phase": "guidance"}}).visible is False
    assert frame_from_kiosk_state({"screen": "failure", "session": None}).visible is False


def test_overlay_uses_only_backend_remaining_time_and_formats_it():
    frame = frame_from_kiosk_state({
        "screen": "playing", "session": {"remaining_seconds": 125},
    })
    assert frame.visible is True
    assert frame.text == "02:05"
    assert frame.remaining_seconds == 125
    assert frame_from_kiosk_state({
        "screen": "playing", "session": {"remaining_seconds": "125"},
    }).visible is False


def test_overlay_has_countdown_visual_thresholds_and_terminal_zero():
    assert flash_interval_ms(121) is None
    assert flash_interval_ms(120) is None
    assert flash_interval_ms(30) == 500
    assert flash_interval_ms(10) == 180
    assert frame_from_kiosk_state({"screen": "ending", "session": {"id": "s1"}}).text == "00:00"
    assert frame_from_kiosk_state({"screen": "ending", "session": {"id": "s1"}}).terminal


def test_overlay_feed_receives_the_sanitized_backend_state_without_a_local_timer():
    state = {"screen": "playing", "session": {"remaining_seconds": 89}}
    received = []
    stopped = False

    class FakeSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def __aiter__(self):
            async def messages():
                yield __import__("json").dumps(state)
            return messages()

    def connect_factory(*args, **kwargs):
        return FakeSocket()

    def emit(value):
        nonlocal stopped
        received.append(value)
        stopped = True

    asyncio.run(receive_kiosk_states("ws://127.0.0.1:8000/api/ws/kiosk", emit,
                                     lambda: stopped, connect_factory))
    assert received == [state]
