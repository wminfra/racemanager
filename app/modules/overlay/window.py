import asyncio
import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from app.core.config import settings
from app.modules.overlay.feed import receive_kiosk_states
from app.modules.overlay.state import flash_interval_ms, frame_from_kiosk_state


class OverlaySocketThread(QThread):
    state_received = Signal(object)

    def run(self):
        url = settings.overlay_backend_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        url = url.rstrip("/") + "/api/ws/kiosk"
        asyncio.run(receive_kiosk_states(url, self.state_received.emit, self.isInterruptionRequested))


class TimerOverlay(QWidget):
    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus |
                 Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(250, 94)
        self._remaining = None
        self._flash_on = False
        self._flash_interval = None
        self._terminal_id = None
        self._terminal_hide_at = None

        self.timer_label = QLabel("00:00", self)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_label.setFont(QFont("Segoe UI", 36, QFont.Weight.Bold))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.addWidget(self.timer_label)
        self._paint_background()

        self.flash_timer = QTimer(self)
        self.flash_timer.timeout.connect(self._toggle_flash)
        self.hide()

    def apply_backend_state(self, state: dict):
        frame = frame_from_kiosk_state(state)
        if not frame.visible:
            self._terminal_id = None
            self._terminal_hide_at = None
            self.flash_timer.stop()
            self.hide()
            return

        if frame.terminal:
            terminal_id = (state.get("session") or {}).get("id")
            if terminal_id != self._terminal_id:
                self._terminal_id = terminal_id
                self._terminal_hide_at = time.monotonic() + 2.0
            if self._terminal_hide_at is not None and time.monotonic() >= self._terminal_hide_at:
                self.hide()
                return
        else:
            self._terminal_id = None
            self._terminal_hide_at = None

        self._remaining = frame.remaining_seconds
        self.timer_label.setText(frame.text)
        self._update_flash_timer()
        self._paint_background()
        if not self.isVisible():
            self._move_to_top_right()
            self.show()

    def _update_flash_timer(self):
        interval = flash_interval_ms(self._remaining)
        if interval is None:
            self.flash_timer.stop()
            self._flash_on = False
        elif interval != self._flash_interval:
            self._flash_interval = interval
            self.flash_timer.setInterval(interval)
            self._flash_on = False
            self.flash_timer.start()

    def _toggle_flash(self):
        self._flash_on = not self._flash_on
        self._paint_background()

    def _paint_background(self):
        if self._remaining is not None and self._remaining <= 30 and self._flash_on:
            background = "rgba(190, 25, 34, 220)"
            border = "rgba(255, 90, 88, 230)"
        elif self._remaining is not None and self._remaining <= 120:
            background = "rgba(7, 12, 18, 220)"
            border = "rgba(255, 200, 87, 210)"
        else:
            background = "rgba(7, 12, 18, 205)"
            border = "rgba(255, 255, 255, 55)"
        self.setStyleSheet(f"TimerOverlay {{ background-color: {background}; border: 1px solid {border}; border-radius: 14px; }} QLabel {{ color: #ffffff; border: none; background: transparent; }}")

    def _move_to_top_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 28, area.top() + 24)
