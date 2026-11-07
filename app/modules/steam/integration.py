from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.core.config import Settings, settings

STEAM_APP_ID = 244210
GAME_PROCESS_NAMES = {"assettocorsa.exe", "acs.exe"}


@dataclass(frozen=True)
class GameProcess:
    pid: int
    name: str
    executable: str


class SteamIntegration:
    """Read-only Steam discovery and narrowly scoped Assetto Corsa process control."""

    def __init__(self, config: Settings = settings):
        self.config = config

    def inspect(self) -> dict:
        steam = self._steam_executable()
        libraries = self._library_roots(steam.parent) if steam else []
        game_dir = self._game_directory(libraries) if steam else None
        steam_running = self._steam_running()
        account_selected = self._account_selected(steam.parent) if steam else False
        installed = bool(game_dir and any((game_dir / name).is_file() for name in GAME_PROCESS_NAMES))
        ready = bool(steam and installed and steam_running and account_selected)
        if not steam:
            reason = "Steam não foi encontrado. Configure RACING_STEAM_PATH."
        elif not installed:
            reason = "Assetto Corsa (AppID 244210) não foi encontrado nas bibliotecas Steam."
        elif not steam_running:
            reason = "Abra a Steam e autentique manualmente na conta dedicada do equipamento."
        elif not account_selected:
            reason = "Não foi possível confirmar uma conta Steam selecionada; autentique manualmente."
        else:
            reason = "Cliente Steam ativo e conta selecionada. A Steam ainda valida a sessão ao iniciar o jogo."
        return {
            "steam_installed": bool(steam),
            "game_installed": installed,
            "steam_running": steam_running,
            "account_selected": account_selected,
            "authentication": "client_active_account_selected" if ready else "not_ready",
            "ready": ready,
            "app_id": STEAM_APP_ID,
            "game_path": str(game_dir) if game_dir else None,
            "reason": reason,
        }

    def launch_game(self) -> None:
        status = self.inspect()
        if not status["ready"]:
            raise RuntimeError(status["reason"])
        if self.find_game_processes():
            raise RuntimeError("Assetto Corsa já está em execução.")
        steam = self._steam_executable()
        assert steam is not None
        subprocess.Popen([str(steam), "-applaunch", str(STEAM_APP_ID)],
                         cwd=str(steam.parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)

    def find_game_processes(self) -> list[GameProcess]:
        status = self.inspect()
        game_dir = Path(status["game_path"]) if status["game_path"] else None
        if not game_dir:
            return []
        found: list[GameProcess] = []
        root = os.path.normcase(os.path.abspath(game_dir)) + os.sep
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                executable = proc.info.get("exe") or ""
                normalized = os.path.normcase(os.path.abspath(executable))
                if name in GAME_PROCESS_NAMES and normalized.startswith(root):
                    found.append(GameProcess(proc.info["pid"], name, executable))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return found

    def is_game_unresponsive(self, processes: list[GameProcess] | None = None) -> bool:
        if sys.platform != "win32":
            return False
        pids = {proc.pid for proc in (processes if processes is not None else self.find_game_processes())}
        if not pids:
            return False
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        hung = False

        def visit(hwnd, _):
            nonlocal hung
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsHungAppWindow(hwnd):
                hung = True
                return False
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return hung

    def close_game(self, grace_seconds: int = 8) -> None:
        processes = self.find_game_processes()
        if not processes:
            return
        pids = {item.pid for item in processes}
        if sys.platform == "win32":
            self._post_close_to_windows(pids)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and self._still_alive(processes):
            time.sleep(0.2)
        for item in processes:
            try:
                proc = psutil.Process(item.pid)
                if self._same_game_path(proc, item.executable):
                    proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass
        current = []
        for item in processes:
            try:
                proc = psutil.Process(item.pid)
                if self._same_game_path(proc, item.executable):
                    current.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        _, alive = psutil.wait_procs(current, timeout=2)
        for proc in alive:
            try:
                if self._same_game_path(proc, next(p.executable for p in processes if p.pid == proc.pid)):
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, StopIteration):
                continue

    @staticmethod
    def _same_game_path(proc: psutil.Process, expected: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(proc.exe())) == os.path.normcase(os.path.abspath(expected))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return False

    @staticmethod
    def _still_alive(processes: list[GameProcess]) -> bool:
        return any(psutil.pid_exists(item.pid) for item in processes)

    @staticmethod
    def _post_close_to_windows(pids: set[int]) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True

        user32.EnumWindows(callback_type(visit), 0)

    def _steam_executable(self) -> Path | None:
        candidates = []
        if self.config.steam_path:
            configured = Path(self.config.steam_path).expanduser()
            candidates.extend([configured, configured / "steam.exe"])
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                    install = Path(winreg.QueryValueEx(key, "SteamPath")[0])
                    candidates.append(install / "steam.exe")
            except (OSError, ImportError):
                pass
            for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
                if root:
                    candidates.append(Path(root) / "Steam" / "steam.exe")
        else:
            candidates.extend([Path.home() / ".steam/steam/steam.sh", Path.home() / ".local/share/Steam/steam.sh"])
        return next((p if p.is_file() else p / "steam.exe" for p in candidates if p.is_file() or (p / "steam.exe").is_file()), None)

    @staticmethod
    def _steam_running() -> bool:
        return any((proc.info.get("name") or "").lower() == "steam.exe"
                   for proc in psutil.process_iter(["name"]))

    @staticmethod
    def _account_selected(steam_dir: Path) -> bool:
        loginusers = steam_dir / "config" / "loginusers.vdf"
        try:
            content = loginusers.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return bool(re.search(r'"MostRecent"\s*"1"', content))

    def _library_roots(self, steam_dir: Path) -> list[Path]:
        roots = [steam_dir]
        manifest = steam_dir / "steamapps" / "libraryfolders.vdf"
        try:
            content = manifest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return roots
        for raw_path in re.findall(r'"path"\s*"([^"]+)"', content):
            path = Path(raw_path.replace("\\\\", "\\"))
            if path not in roots:
                roots.append(path)
        return roots

    def _game_directory(self, libraries: list[Path]) -> Path | None:
        for library in libraries:
            appmanifest = library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
            try:
                content = appmanifest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = re.search(r'"installdir"\s*"([^"]+)"', content, re.IGNORECASE)
            if match:
                directory = library / "steamapps" / "common" / match.group(1)
                if directory.is_dir():
                    return directory
        if self.config.assetto_corsa_path:
            configured = Path(self.config.assetto_corsa_path).expanduser()
            if configured.is_dir():
                return configured
        return None
