import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.modules.overlay.guard import OverlayInstanceGuard
from app.modules.overlay.window import TimerOverlay


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_overlay_window_starts_hidden_non_focusable_and_click_through(qapp):
    overlay = TimerOverlay()
    assert not overlay.isVisible()
    assert overlay.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.close()


def test_overlay_window_shows_backend_time_and_hides_when_game_ends(qapp):
    overlay = TimerOverlay()
    overlay.apply_backend_state({"screen": "playing", "session": {"id": "x", "remaining_seconds": 71}})
    assert overlay.isVisible()
    assert overlay.timer_label.text() == "01:11"
    overlay.apply_backend_state({"screen": "waiting", "session": None})
    assert not overlay.isVisible()
    overlay.close()


def test_overlay_single_instance_guard_prevents_duplicate_process(qapp, tmp_path):
    first = OverlayInstanceGuard(tmp_path / "overlay.lock")
    second = OverlayInstanceGuard(tmp_path / "overlay.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
