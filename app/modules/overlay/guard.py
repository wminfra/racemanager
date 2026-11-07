from pathlib import Path

from PySide6.QtCore import QLockFile


class OverlayInstanceGuard:
    def __init__(self, lock_path: str | Path):
        self.lock = QLockFile(str(lock_path))

    def acquire(self) -> bool:
        return self.lock.tryLock(0)

    def release(self) -> None:
        if self.lock.isLocked():
            self.lock.unlock()
