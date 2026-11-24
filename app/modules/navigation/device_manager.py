from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Known maker/hobbyist controller VID/PID pairs
KNOWN_DEVICE_NAMES: dict[tuple[int, int], str] = {
    (0x2341, 0x8036): "Arduino Leonardo",
    (0x2341, 0x8037): "Arduino Micro",
    (0x2341, 0x0036): "Arduino Leonardo",
    (0x2341, 0x0037): "Arduino Micro",
    (0x1B4F, 0x9206): "SparkFun Pro Micro",
    (0x1B4F, 0x9205): "SparkFun Pro Micro",
    (0x045E, 0x028E): "Xbox 360 Controller",
    (0x045E, 0x02EA): "Xbox One Controller",
    (0x045E, 0x0B12): "Xbox Wireless Controller",
    (0x054C, 0x09CC): "Sony DualShock 4 Controller",
    (0x054C, 0x0CE6): "Sony DualSense Controller",
}


@dataclass(frozen=True)
class JoystickDevice:
    id: str
    index: int
    name: str
    hardware_id: str
    vendor_id: int
    product_id: int
    num_buttons: int
    connected: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeviceManager:
    """Discovers and queries joysticks using the non-exclusive Windows Multimedia Joystick API."""

    def __init__(self) -> None:
        self._is_windows = sys.platform == "win32"
        if self._is_windows:
            import ctypes
            from ctypes import wintypes

            self._winmm = ctypes.windll.winmm

            class JOYCAPSW(ctypes.Structure):
                _fields_ = [
                    ("wMid", wintypes.WORD),
                    ("wPid", wintypes.WORD),
                    ("szPname", wintypes.WCHAR * 32),
                    ("wXmin", wintypes.UINT),
                    ("wXmax", wintypes.UINT),
                    ("wYmin", wintypes.UINT),
                    ("wYmax", wintypes.UINT),
                    ("wZmin", wintypes.UINT),
                    ("wZmax", wintypes.UINT),
                    ("wNumButtons", wintypes.UINT),
                    ("wPeriodMin", wintypes.UINT),
                    ("wPeriodMax", wintypes.UINT),
                    ("wRmin", wintypes.UINT),
                    ("wRmax", wintypes.UINT),
                    ("wUmin", wintypes.UINT),
                    ("wUmax", wintypes.UINT),
                    ("wVmin", wintypes.UINT),
                    ("wVmax", wintypes.UINT),
                    ("wCaps", wintypes.UINT),
                    ("wMaxAxes", wintypes.UINT),
                    ("wNumAxes", wintypes.UINT),
                    ("wMaxButtons", wintypes.UINT),
                    ("szRegKey", wintypes.WCHAR * 32),
                    ("szOEMVxD", wintypes.WCHAR * 260),
                ]

            class JOYINFOEX(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("dwXpos", wintypes.DWORD),
                    ("dwYpos", wintypes.DWORD),
                    ("dwZpos", wintypes.DWORD),
                    ("dwRpos", wintypes.DWORD),
                    ("dwUpos", wintypes.DWORD),
                    ("dwVpos", wintypes.DWORD),
                    ("dwButtons", wintypes.DWORD),
                    ("dwButtonNumber", wintypes.DWORD),
                    ("dwPOV", wintypes.DWORD),
                    ("dwReserved1", wintypes.DWORD),
                    ("dwReserved2", wintypes.DWORD),
                ]

            self._JOYCAPSW = JOYCAPSW
            self._JOYINFOEX = JOYINFOEX
            self._JOY_RETURNBUTTONS = 0x00000080
            self._JOY_RETURNALL = 0x000000FF
        else:
            self._winmm = None

    def _resolve_friendly_name(self, mid: int, pid: int, fallback: str) -> str:
        if (mid, pid) in KNOWN_DEVICE_NAMES:
            return KNOWN_DEVICE_NAMES[(mid, pid)]

        if self._is_windows:
            try:
                import winreg

                vid_pid_str = f"VID_{mid:04X}&PID_{pid:04X}".upper()
                for bus in ("USB", "HID"):
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Enum\{bus}") as k:
                            num_keys, _, _ = winreg.QueryInfoKey(k)
                            for i in range(num_keys):
                                subkey = winreg.EnumKey(k, i)
                                if vid_pid_str in subkey.upper():
                                    with winreg.OpenKey(k, subkey) as dev_k:
                                        n_inst, _, _ = winreg.QueryInfoKey(dev_k)
                                        for j in range(n_inst):
                                            inst = winreg.EnumKey(dev_k, j)
                                            with winreg.OpenKey(dev_k, inst) as inst_k:
                                                for val_name in ("FriendlyName", "DeviceDesc"):
                                                    try:
                                                        res, _ = winreg.QueryValueEx(inst_k, val_name)
                                                        if res:
                                                            clean = res.split(";")[-1] if ";" in res else res
                                                            if not clean.startswith("@"):
                                                                return clean
                                                    except OSError:
                                                        pass
                    except OSError:
                        continue
            except Exception:
                pass
        return fallback or f"Joystick Genérico (VID:{mid:04X}&PID:{pid:04X})"

    def list_devices(self) -> list[JoystickDevice]:
        if not self._is_windows or not self._winmm:
            return []

        import ctypes

        devices: list[JoystickDevice] = []
        try:
            num_devs = min(self._winmm.joyGetNumDevs(), 16)
        except Exception:
            return []

        for index in range(num_devs):
            caps = self._JOYCAPSW()
            res = self._winmm.joyGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps))
            if res == 0:
                hardware_id = f"VID_{caps.wMid:04X}&PID_{caps.wPid:04X}"
                friendly_name = self._resolve_friendly_name(caps.wMid, caps.wPid, caps.szPname)
                connected = self.is_connected(index)
                device_id = f"joy_{index}_{caps.wMid:04x}_{caps.wPid:04x}"

                devices.append(
                    JoystickDevice(
                        id=device_id,
                        index=index,
                        name=friendly_name,
                        hardware_id=hardware_id,
                        vendor_id=caps.wMid,
                        product_id=caps.wPid,
                        num_buttons=max(1, min(caps.wNumButtons, 32)),
                        connected=connected,
                    )
                )
        return devices

    def get_device(self, identifier: str | int | None) -> JoystickDevice | None:
        if identifier is None:
            return None
        all_devs = self.list_devices()
        if isinstance(identifier, int):
            for dev in all_devs:
                if dev.index == identifier:
                    return dev
            return None

        clean_id = str(identifier).strip()
        # Direct match on device id
        for dev in all_devs:
            if dev.id.casefold() == clean_id.casefold():
                return dev
        # Match on hardware_id (e.g. VID_2341&PID_8037)
        for dev in all_devs:
            if dev.hardware_id.casefold() == clean_id.casefold():
                return dev
        # Match on index if passed as string
        if clean_id.isdigit():
            idx = int(clean_id)
            for dev in all_devs:
                if dev.index == idx:
                    return dev
        return None

    def is_connected(self, index: int) -> bool:
        if not self._is_windows or not self._winmm:
            return False
        import ctypes

        info = self._JOYINFOEX()
        info.dwSize = ctypes.sizeof(info)
        info.dwFlags = self._JOY_RETURNBUTTONS
        try:
            res = self._winmm.joyGetPosEx(index, ctypes.byref(info))
            return res == 0  # JOYERR_NOERROR
        except Exception:
            return False

    def poll_buttons(self, index: int) -> tuple[bool, int]:
        """Poll the current button bitmask of device at index. Returns (connected, dwButtons)."""
        if not self._is_windows or not self._winmm:
            return False, 0
        import ctypes

        info = self._JOYINFOEX()
        info.dwSize = ctypes.sizeof(info)
        info.dwFlags = self._JOY_RETURNBUTTONS
        try:
            res = self._winmm.joyGetPosEx(index, ctypes.byref(info))
            if res == 0:
                return True, int(info.dwButtons)
            return False, 0
        except Exception:
            return False, 0
