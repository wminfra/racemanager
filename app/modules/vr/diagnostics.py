from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from app.core.config import Settings, settings
from app.modules.steam.integration import SteamIntegration
from app.modules.vr.meta_runtime import query_meta_runtime

_OPENVR_LOCK = threading.Lock()
_STEAMVR_APP_ID = 250820


def _state(value: bool | None) -> str:
    return "available" if value is True else "unavailable" if value is False else "unknown"


class VRDiagnostics:
    """Collects local VR state without launching, stopping, or changing runtimes."""

    def __init__(self, config: Settings = settings, steam: SteamIntegration | None = None):
        self.config = config
        self.steam = steam or SteamIntegration(config)
        self._meta_cache: tuple[float, dict] | None = None

    def inspect(self, mode: str = "oculus") -> dict[str, Any]:
        if mode not in {"oculus", "steamvr"}:
            raise ValueError("Modo VR invÃ¡lido.")

        steam_status = self.steam.inspect()
        steamvr_root = self._steamvr_root()
        steamvr_installed = bool(steamvr_root and (steamvr_root / "bin" / "win64" / "vrserver.exe").is_file())
        steamvr_running = self._process_running("vrserver.exe", steamvr_root)
        openvr = self._openvr_status(steamvr_root, steamvr_running) if mode == "steamvr" else {
            "runtime_query": "not_requested", "hmd_connected": "unknown",
            "hmd_class": "unknown", "tracking_valid": "unknown", "tracking_result": "unknown",
            "error": "A sondagem OpenVR sÃ³ Ã© consultada no modo SteamVR."}

        meta_root = self._meta_root()
        client_installed = bool(meta_root and (meta_root / "Support" / "oculus-client" / "OculusClient.exe").is_file())
        runtime_installed = bool(meta_root and (meta_root / "Support" / "oculus-runtime" / "LibOVRRT64_1.dll").is_file())
        runtime_service = self._oculus_service_state()
        meta_client_running = self._process_running("OculusClient.exe", meta_root) if client_installed else None
        dash_running = self._process_running("OculusDash.exe", meta_root) if meta_root else None

        acc_running = bool(self.steam.find_game_processes())
        meta_probe = (self._meta_status(meta_root, runtime_service, acc_running) if mode == "oculus" else {
            "runtime": "unknown", "connected": "unknown", "tracking": "unknown",
            "worn": "unknown", "source": "Meta PC SDK não consultado neste modo.", "detail": ""})

        hmd_connected = (openvr["hmd_connected"] if mode == "steamvr"
                         else meta_probe["connected"])
        tracked = (openvr["tracking_valid"] if mode == "steamvr"
                   else meta_probe["tracking"])
        pcvr = self._pcvr_status(mode, steamvr_running, hmd_connected, tracked, meta_probe)
        return {
            "mode": mode,
            "mode_label": "SteamVR" if mode == "steamvr" else "VR Oculus",
            "steam": {"installed": _state(steam_status.get("steam_installed")),
                      "running": _state(steam_status.get("steam_running"))},
            "acc": {"installed": _state(steam_status.get("game_installed")),
                    "running": _state(bool(self.steam.find_game_processes()))},
            "meta_link": {"installed": _state(client_installed),
                          "client_running": _state(meta_client_running),
                          "dashboard_running": _state(dash_running),
                          "runtime_installed": _state(runtime_installed),
                          "runtime_service": runtime_service,
                          "headset_connected": meta_probe["connected"],
                          "headset_worn": meta_probe["worn"],
                          "tracking": meta_probe["tracking"],
                          "source": meta_probe["source"],
                          "detail": meta_probe["detail"],
                          "note": "Processos e serviço não são usados como prova de conexão."},
            "steamvr": {"installed": _state(steamvr_installed),
                        "runtime_running": _state(steamvr_running),
                        "hmd_connected": hmd_connected,
                        "hmd_class": openvr["hmd_class"],
                        "tracking_valid": tracked,
                        "tracking_result": openvr["tracking_result"],
                        "headset_worn": "unknown",
                        "query": openvr["runtime_query"],
                        "detail": openvr["error"]},
            "overall": self._overall(mode, steam_status, runtime_installed, runtime_service,
                                     hmd_connected, tracked, steamvr_running),
            "pcvr": pcvr,
            "read_only": True,
        }

    def _meta_status(self, root: Path | None, service: str, acc_running: bool = False) -> dict:
        if not root or not (root / "Support" / "oculus-runtime" / "LibOVRRT64_1.dll").is_file():
            return {"runtime": "unavailable", "connected": "unknown", "tracking": "unknown",
                    "worn": "unknown", "source": "Meta PC SDK (LibOVR)",
                    "detail": "Runtime Meta não instalado ou biblioteca ausente."}
        if service == "unavailable":
            return {"runtime": "unavailable", "connected": "unknown", "tracking": "unknown",
                    "worn": "unknown", "source": "Meta PC SDK (LibOVR)",
                    "detail": "O serviço do runtime Meta não está disponível; headset não foi presumido desconectado."}
        if service != "available":
            return {"runtime": "unknown", "connected": "unknown", "tracking": "unknown",
                    "worn": "unknown", "source": "Meta PC SDK (LibOVR)",
                    "detail": "Não foi possível confirmar o estado do serviço necessário para consultar o runtime."}
        if acc_running:
            return {"runtime": "available", "connected": "unknown", "tracking": "unknown",
                    "worn": "unknown", "source": "Meta PC SDK (LibOVR)",
                    "detail": "Consulta pausada enquanto o ACC está ativo para não disputar a sessão do runtime."}
        now = time.monotonic()
        if self._meta_cache and now - self._meta_cache[0] < 10:
            return self._meta_cache[1]
        result = query_meta_runtime(root)
        self._meta_cache = (now, result)
        return result

    @staticmethod
    def _pcvr_status(mode: str, steamvr_running: bool | None, connected: str,
                     tracking: str, meta: dict) -> dict[str, str]:
        if mode == "oculus":
            runtime = meta["runtime"]
            source = meta["source"]
            detail = meta["detail"]
            if runtime == "unavailable":
                return {"state": "runtime_unavailable", "label": "RUNTIME INDISPONÍVEL",
                        "source": source, "detail": detail}
            if connected == "connected" and tracking == "available":
                return {"state": "connected", "label": "CONECTADO", "source": source, "detail": detail}
            if connected == "disconnected":
                return {"state": "disconnected", "label": "DESCONECTADO", "source": source, "detail": detail}
            return {"state": "unknown", "label": "ESTADO DESCONHECIDO", "source": source, "detail": detail}
        source = "Valve OpenVR (SteamVR)"
        if steamvr_running is not True:
            state = "runtime_unavailable" if steamvr_running is False else "unknown"
            return {"state": state, "label": "RUNTIME INDISPONÍVEL" if state == "runtime_unavailable" else "ESTADO DESCONHECIDO",
                    "source": source, "detail": "SteamVR não está em execução; a consulta não inicia o runtime."}
        if connected == "unavailable":
            return {"state": "disconnected", "label": "DESCONECTADO", "source": source,
                    "detail": "OpenVR informa que não há HMD conectado."}
        if connected == "available" and tracking == "available":
            return {"state": "connected", "label": "CONECTADO", "source": source,
                    "detail": "HMD conectado e pose de tracking válida."}
        return {"state": "unknown", "label": "ESTADO DESCONHECIDO", "source": source,
                "detail": "OpenVR não confirmou conexão e tracking válidos."}

    @staticmethod
    def _overall(mode: str, steam: dict, meta_installed: bool, service: str,
                 connected: str, tracked: str, steamvr_running: bool | None) -> str:
        if mode == "steamvr":
            if steamvr_running is False:
                return "runtime_unavailable"
            if steamvr_running is None:
                return "unknown"
            if connected == "unavailable":
                return "headset_not_connected"
            if tracked == "available":
                return "ready"
            return "unknown"
        if not meta_installed:
            return "runtime_unavailable"
        if service == "unavailable":
            return "runtime_unavailable"
        if connected == "connected" and tracked == "available":
            return "ready"
        if connected == "disconnected":
            return "headset_not_connected"
        return "unknown"

    def _steam_root(self) -> Path | None:
        try:
            executable = self.steam._steam_executable()
            return executable.parent if executable else None
        except (OSError, AttributeError):
            return None

    def _steamvr_root(self) -> Path | None:
        root = self._steam_root()
        if not root:
            return None
        libraries = self.steam._library_roots(root)
        for library in libraries:
            manifest = library / "steamapps" / f"appmanifest_{_STEAMVR_APP_ID}.acf"
            if not manifest.is_file():
                continue
            try:
                text = manifest.read_text(encoding="utf-8", errors="ignore")
                import re
                match = re.search(r'"installdir"\s*"([^"]+)"', text, re.IGNORECASE)
                if match:
                    candidate = library / "steamapps" / "common" / match.group(1)
                    if candidate.is_dir():
                        return candidate
            except OSError:
                continue
        return None

    def _meta_root(self) -> Path | None:
        candidates: list[Path] = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            parent = os.environ.get(variable)
            if parent:
                candidates.append(Path(parent) / "Oculus")
        candidates.extend((Path("D:/Meta Horizon"), Path("C:/Program Files/Oculus")))
        for root in candidates:
            if (root / "Support" / "oculus-runtime").is_dir():
                return root
        return None

    @staticmethod
    def _process_running(name: str, expected_root: Path | None) -> bool | None:
        if not expected_root:
            return None
        expected_root = expected_root.resolve()
        seen = False
        for process in psutil.process_iter(["name", "exe"]):
            try:
                if (process.info.get("name") or "").casefold() != name.casefold():
                    continue
                executable = process.info.get("exe")
                if not executable:
                    seen = True
                    continue
                actual = Path(executable).resolve()
                if actual == expected_root or expected_root in actual.parents:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return False if not seen else None

    @staticmethod
    def _oculus_service_state() -> str:
        if os.name != "nt":
            return "unknown"
        try:
            service = psutil.win_service_get("OVRService")
            status = service.status().casefold()
            return "available" if status == "running" else "unavailable" if status in {"stopped", "stop_pending"} else "unknown"
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return "unknown"

    @staticmethod
    def _openvr_status(steamvr_root: Path | None, runtime_running: bool | None) -> dict[str, str]:
        result = {"runtime_query": "not_available", "hmd_connected": "unknown",
                  "hmd_class": "unknown", "tracking_valid": "unknown",
                  "tracking_result": "unknown", "error": "OpenVR indisponÃ­vel."}
        if runtime_running is not True:
            result.update(runtime_query="not_queried", error="SteamVR nÃ£o estÃ¡ confirmado em execuÃ§Ã£o; a sondagem nÃ£o inicia o runtime.")
            return result
        try:
            import openvr
        except ImportError:
            result["error"] = "DependÃªncia OpenVR ausente; instale as dependÃªncias do Racing Manager."
            return result
        if not steamvr_root:
            result["error"] = "DiretÃ³rio instalado do SteamVR nÃ£o encontrado."
            return result

        # OpenVR writes its client log to Steam's installation by default. Use a
        # temporary registry override so diagnostics remain local and do not
        # alter the user's SteamVR/OpenXR configuration.
        temp_root = Path(tempfile.gettempdir()) / "racing-manager-vr-diagnostic"
        log_dir = temp_root / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path_registry = temp_root / "openvrpaths.vrpath"
            payload = {"config": [str(steamvr_root.parent.parent.parent / "config")],
                       "external_drivers": [], "jsonid": "vrpathreg", "log": [str(log_dir)],
                       "runtime": [str(steamvr_root)], "version": 1}
            path_registry.write_text(json.dumps(payload), encoding="utf-8")
            with _OPENVR_LOCK:
                previous = os.environ.get("VR_PATHREG_OVERRIDE")
                os.environ["VR_PATHREG_OVERRIDE"] = str(path_registry)
                system = None
                try:
                    system = openvr.init(openvr.VRApplication_Background)
                    index = openvr.k_unTrackedDeviceIndex_Hmd
                    connected = bool(system.isTrackedDeviceConnected(index))
                    result["runtime_query"] = "success"
                    result["hmd_connected"] = _state(connected)
                    if connected:
                        device_class = system.getTrackedDeviceClass(index)
                        result["hmd_class"] = "hmd" if device_class == openvr.TrackedDeviceClass_HMD else f"class_{device_class}"
                        poses = system.getDeviceToAbsoluteTrackingPose(
                            openvr.TrackingUniverseStanding, 0.0, openvr.k_unMaxTrackedDeviceCount)
                        pose = poses[index]
                        valid = bool(pose.bPoseIsValid) and pose.eTrackingResult == openvr.TrackingResult_Running_OK
                        result["tracking_valid"] = _state(valid)
                        result["tracking_result"] = VRDiagnostics._tracking_result_name(openvr, pose.eTrackingResult)
                    else:
                        result["hmd_class"] = "unknown"
                        result["tracking_valid"] = "unavailable"
                        result["tracking_result"] = "no_hmd"
                    result["error"] = ""
                finally:
                    if system is not None:
                        openvr.shutdown()
                    if previous is None:
                        os.environ.pop("VR_PATHREG_OVERRIDE", None)
                    else:
                        os.environ["VR_PATHREG_OVERRIDE"] = previous
        except Exception as exc:  # OpenVR exposes runtime-specific errors.
            result.update(runtime_query="failed", error=f"OpenVR nÃ£o confirmou o HMD: {type(exc).__name__}.")
        return result

    @staticmethod
    def _tracking_result_name(openvr: Any, value: int) -> str:
        names = ("Uninitialized", "Calibrating_InProgress", "Calibrating_OutOfRange",
                 "Running_OK", "Running_OutOfRange")
        for name in names:
            if getattr(openvr, f"TrackingResult_{name}", object()) == value:
                return name
        return "Unknown"
