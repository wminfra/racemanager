import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.modules.overlay.guard import OverlayInstanceGuard
from app.modules.overlay.window import OverlaySocketThread, TimerOverlay


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    runtime = root / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    guard = OverlayInstanceGuard(runtime / "overlay.lock")
    if not guard.acquire():
        return 0

    application = QApplication(sys.argv)
    application.setApplicationName("Racing Manager Overlay")
    application.setQuitOnLastWindowClosed(False)
    window = TimerOverlay()
    socket_thread = OverlaySocketThread()
    socket_thread.state_received.connect(window.apply_backend_state)
    socket_thread.start()
    application.aboutToQuit.connect(socket_thread.requestInterruption)
    application.aboutToQuit.connect(socket_thread.wait)
    application.aboutToQuit.connect(guard.release)
    result = application.exec()
    socket_thread.wait(3000)
    guard.release()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
