"""Read-only Meta PC SDK probe for Link HMD presence, mount and tracking."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


class _Posef(ctypes.Structure):
    _fields_ = [("orientation", ctypes.c_float * 4), ("position", ctypes.c_float * 3)]


class _PoseStatef(ctypes.Structure):
    _fields_ = [
        ("pose", _Posef), ("angular_velocity", ctypes.c_float * 3),
        ("linear_velocity", ctypes.c_float * 3), ("angular_acceleration", ctypes.c_float * 3),
        ("linear_acceleration", ctypes.c_float * 3), ("padding", ctypes.c_uint32),
        ("time_in_seconds", ctypes.c_double),
    ]


class _TrackingState(ctypes.Structure):
    _fields_ = [
        ("head_pose", _PoseStatef), ("status_flags", ctypes.c_uint32),
        ("hand_poses", _PoseStatef * 2), ("hand_status_flags", ctypes.c_uint32 * 2),
        ("calibrated_origin", _Posef),
    ]


class _SessionStatus(ctypes.Structure):
    _fields_ = [(name, ctypes.c_byte) for name in
                ("visible", "hmd_present", "hmd_mounted", "display_lost", "should_quit", "should_recenter")]


def query_meta_runtime(runtime_root: Path) -> dict[str, str | bool]:
    """Query through the installed Oculus PC SDK; return unknown on any ABI/runtime error.

    The service must already be running. The probe creates and immediately destroys
    a short-lived SDK session to obtain current HMD and tracking state; it never
    starts Meta Link, SteamVR, or a game.
    """
    unknown = {"runtime": "unknown", "connected": "unknown", "tracking": "unknown",
               "worn": "unknown", "source": "Meta PC SDK (LibOVR)", "detail": ""}
    if os.name != "nt":
        return {**unknown, "detail": "A consulta Meta PC SDK só funciona no Windows."}
    dll_path = runtime_root / "Support" / "oculus-runtime" / "LibOVRRT64_1.dll"
    if not dll_path.is_file():
        return {**unknown, "runtime": "unavailable", "detail": "Biblioteca LibOVR do runtime Meta não encontrada."}
    session = ctypes.c_void_p()
    initialized = False
    try:
        with os.add_dll_directory(str(dll_path.parent)):
            lib = ctypes.CDLL(str(dll_path))
            lib.ovr_Initialize.argtypes = [ctypes.c_void_p]
            lib.ovr_Initialize.restype = ctypes.c_int
            lib.ovr_Create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
            lib.ovr_Create.restype = ctypes.c_int
            lib.ovr_GetSessionStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SessionStatus)]
            lib.ovr_GetSessionStatus.restype = ctypes.c_int
            lib.ovr_GetTrackingState.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_byte]
            lib.ovr_GetTrackingState.restype = _TrackingState
            lib.ovr_Destroy.argtypes = [ctypes.c_void_p]
            lib.ovr_Destroy.restype = None
            lib.ovr_Shutdown.argtypes = []
            lib.ovr_Shutdown.restype = None

            result = lib.ovr_Initialize(None)
            if result < 0:
                return {**unknown, "runtime": "unavailable", "detail": f"ovr_Initialize retornou {result}."}
            initialized = True
            luid = ctypes.create_string_buffer(8)
            result = lib.ovr_Create(ctypes.byref(session), ctypes.byref(luid))
            if result < 0:
                return {**unknown, "runtime": "available", "connected": "disconnected",
                        "tracking": "unavailable", "detail": f"Runtime ativo; o SDK não reconheceu HMD (ovr_Create={result})."}
            status = _SessionStatus()
            result = lib.ovr_GetSessionStatus(session, ctypes.byref(status))
            if result < 0:
                return {**unknown, "runtime": "available", "detail": f"ovr_GetSessionStatus retornou {result}."}
            if not status.hmd_present:
                return {**unknown, "runtime": "available", "connected": "disconnected",
                        "tracking": "unavailable", "worn": "unavailable", "detail": "Meta PC SDK informa HMD ausente."}
            tracking = lib.ovr_GetTrackingState(session, 0.0, 0)
            orientation_tracked = bool(tracking.status_flags & 0x01)
            position_tracked = bool(tracking.status_flags & 0x02)
            return {**unknown, "runtime": "available", "connected": "connected" if orientation_tracked else "unknown",
                    "tracking": "available" if orientation_tracked else "unavailable",
                    "worn": "available" if status.hmd_mounted else "unavailable",
                    "detail": f"HMD presente; tracking orientação={orientation_tracked}, posição={position_tracked}."}
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        return {**unknown, "detail": f"Consulta Meta PC SDK inconclusiva ({type(exc).__name__})."}
    finally:
        if initialized:
            try:
                if session.value:
                    lib.ovr_Destroy(session)
                lib.ovr_Shutdown()
            except Exception:
                pass
