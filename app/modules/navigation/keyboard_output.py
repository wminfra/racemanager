from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod

from app.modules.navigation.constants import ACTIONS, ALLOWED_ACTIONS

logger = logging.getLogger(__name__)


class KeyboardOutputInterface(ABC):
    @abstractmethod
    def send_action(self, action: str, hold_duration_seconds: float = 0.03) -> bool:
        """Simulate keyboard input for the given navigation action."""


class WindowsKeyboardOutput(KeyboardOutputInterface):
    """Dispatches virtual keystrokes using Win32 SendInput."""

    def __init__(self) -> None:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            class HARDWAREINPUT(ctypes.Structure):
                _fields_ = [
                    ("uMsg", wintypes.DWORD),
                    ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD),
                ]

            class _INPUTunion(ctypes.Union):
                _fields_ = [
                    ("mi", MOUSEINPUT),
                    ("ki", KEYBDINPUT),
                    ("hi", HARDWAREINPUT),
                ]

            class INPUT(ctypes.Structure):
                _fields_ = [
                    ("type", wintypes.DWORD),
                    ("u", _INPUTunion),
                ]

            self._INPUT = INPUT
            self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            self._user32.SendInput.restype = wintypes.UINT
            self._available = True
        else:
            self._available = False

    def _ensure_desktop(self) -> None:
        """Attach the calling thread to the interactive desktop if necessary."""
        if not self._available:
            return
        try:
            desk = self._user32.OpenInputDesktop(0, False, 0x01FF)
            if desk:
                self._user32.SetThreadDesktop(desk)
        except Exception:
            pass

    def send_action(self, action: str, hold_duration_seconds: float = 0.03) -> bool:
        if action not in ACTIONS:
            raise ValueError(f"Ação não permitida para navegação: {action!r}. Permitidas: {ALLOWED_ACTIONS}")

        if not self._available:
            logger.warning("[NAV-KEYBOARD] WindowsKeyboardOutput não disponível nesta plataforma.")
            return False

        import ctypes

        info = ACTIONS[action]
        vk = info.vk_code
        scan = info.scan_code or self._user32.MapVirtualKeyW(vk, 0)
        flags_down = 1 if info.is_extended else 0  # KEYEVENTF_EXTENDEDKEY = 0x0001
        flags_up = flags_down | 2                  # KEYEVENTF_KEYUP = 0x0002

        inp_down = self._INPUT()
        inp_down.type = 1  # INPUT_KEYBOARD
        inp_down.u.ki.wVk = vk
        inp_down.u.ki.wScan = scan
        inp_down.u.ki.dwFlags = flags_down

        inp_up = self._INPUT()
        inp_up.type = 1
        inp_up.u.ki.wVk = vk
        inp_up.u.ki.wScan = scan
        inp_up.u.ki.dwFlags = flags_up

        self._ensure_desktop()
        self._kernel32.SetLastError(0)
        res_down = self._user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(self._INPUT))
        time.sleep(max(0.01, hold_duration_seconds))
        res_up = self._user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(self._INPUT))

        success = (res_down == 1 and res_up == 1)
        if not success:
            err = self._kernel32.GetLastError()
            logger.error("[NAV-KEYBOARD] SendInput falhou para ação %s (res_down=%s, res_up=%s, err=%s)",
                         action, res_down, res_up, err)
        else:
            logger.debug("[NAV-KEYBOARD] Ação enviada com sucesso: %s (%s)", action, info.command_name)
        return success


class MockKeyboardOutput(KeyboardOutputInterface):
    """In-memory mock for testing and non-Windows environments."""

    def __init__(self) -> None:
        self.sent_actions: list[tuple[str, float]] = []
        self.should_succeed: bool = True

    def send_action(self, action: str, hold_duration_seconds: float = 0.03) -> bool:
        if action not in ACTIONS:
            raise ValueError(f"Ação não permitida para navegação: {action!r}. Permitidas: {ALLOWED_ACTIONS}")
        if not self.should_succeed:
            return False
        self.sent_actions.append((action, time.time()))
        return True


def get_default_keyboard_output() -> KeyboardOutputInterface:
    if sys.platform == "win32":
        return WindowsKeyboardOutput()
    return MockKeyboardOutput()
