from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.core.config import Settings, settings
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE, GameDefinition


@dataclass(frozen=True)
class GameProcess:
    pid: int
    name: str
    executable: str


class SteamIntegration:
    """Steam discovery plus process control limited to the configured game's executable."""

    def __init__(self, config: Settings = settings, game: GameDefinition = ASSETTO_CORSA_COMPETIZIONE):
        self.config = config
        self.game = game
        self._cached_root: Path | None = None
        self._cached_executable: Path | None = None

    def inspect(self) -> dict:
        steam_executable = self._steam_executable()
        steam_root = steam_executable.parent if steam_executable else None
        library_roots = self._library_roots(steam_root) if steam_root else []
        install_root = self._install_root(library_roots) if steam_root else None
        executable = self._game_executable(install_root) if install_root else None
        accessible = bool(executable and executable.is_file() and os.access(executable, os.R_OK))
        steam_running = self._steam_running()
        account_selected = self._account_selected(steam_root) if steam_root else False
        game_installed = bool(install_root and executable)
        ready = bool(steam_executable and steam_running and game_installed and accessible and account_selected)
        if not steam_executable:
            reason = "Cliente Steam não encontrado. Configure RACING_STEAM_PATH."
        elif not steam_running:
            reason = "Abra a Steam e autentique manualmente a conta desejada."
        elif not install_root or not game_installed:
            reason = f"{self.game.name} (AppID {self.game.app_id}) não foi encontrado. Configure RACING_STEAM_LIBRARY_PATH se necessário."
        elif not accessible:
            reason = "A instalação do jogo existe, mas o executável não está acessível para leitura."
        elif not account_selected:
            reason = "Não foi detectada uma conta Steam selecionada. Abra a Steam e autentique manualmente."
        else:
            reason = "Steam ativa, ACC instalado e executável acessível. A Steam validará a sessão ao abrir o jogo."
        return {
            "steam_installed": bool(steam_executable),
            "steam_running": steam_running,
            "account_selected": account_selected,
            "game_installed": game_installed,
            "installation_accessible": accessible,
            "ready": ready,
            "app_id": self.game.app_id,
            "launch_uri": self.game.launch_uri,
            "game_path": str(install_root) if install_root else None,
            "game_executable": str(executable) if executable else None,
            "reason": reason,
        }

    def launch_game(self) -> None:
        status = self.inspect()
        if not status["ready"]:
            raise RuntimeError(status["reason"])
        if self.find_game_processes():
            raise RuntimeError(f"{self.game.name} já está em execução.")
        if not hasattr(os, "startfile"):
            raise RuntimeError("A abertura do protocolo Steam requer Windows.")
        try:
            os.startfile(self.game.launch_uri)  # type: ignore[attr-defined]
        except OSError as exc:
            raise RuntimeError(f"Não foi possível abrir {self.game.launch_uri}: {exc}") from exc

    def find_game_processes(self) -> list[GameProcess]:
        status = self.inspect()
        executable = status["game_executable"]
        if not executable:
            return []
        expected = self._normalize_path(executable)
        found: list[GameProcess] = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = proc.info.get("name") or ""
                process_executable = proc.info.get("exe") or ""
                if name.casefold() == self.game.executable_name.casefold() and self._normalize_path(process_executable) == expected:
                    found.append(GameProcess(proc.info["pid"], name, process_executable))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return found

    def is_game_unresponsive(self, processes: list[GameProcess] | None = None) -> bool:
        if sys.platform != "win32":
            return False
        pids = {item.pid for item in (processes if processes is not None else self.find_game_processes())}
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
        while time.monotonic() < deadline and self._same_processes_alive(processes):
            time.sleep(0.2)
        candidates = []
        for item in processes:
            try:
                proc = psutil.Process(item.pid)
                if self._normalize_path(proc.exe()) == self._normalize_path(item.executable):
                    candidates.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        for proc in candidates:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        _, alive = psutil.wait_procs(candidates, timeout=2)
        for proc in alive:
            try:
                expected = next(item.executable for item in processes if item.pid == proc.pid)
                if self._normalize_path(proc.exe()) == self._normalize_path(expected):
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, StopIteration):
                continue

    @staticmethod
    def _same_processes_alive(processes: list[GameProcess]) -> bool:
        for item in processes:
            try:
                proc = psutil.Process(item.pid)
                if SteamIntegration._normalize_path(proc.exe()) == SteamIntegration._normalize_path(item.executable):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return False

    @staticmethod
    def _normalize_path(value: str | Path) -> str:
        return os.path.normcase(os.path.abspath(str(value)))

    def _steam_executable(self) -> Path | None:
        candidates: list[Path] = []
        if self.config.steam_path:
            configured = Path(self.config.steam_path).expanduser()
            candidates.extend([configured, configured / "steam.exe"])
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                    candidates.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]) / "steam.exe")
            except (OSError, ImportError):
                pass
            for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
                root = os.environ.get(env_name)
                if root:
                    candidates.append(Path(root) / "Steam" / "steam.exe")
        else:
            candidates.extend([Path.home() / ".steam/steam/steam.sh", Path.home() / ".local/share/Steam/steam.sh"])
        discovered = shutil.which("steam")
        if discovered:
            candidates.append(Path(discovered))
        for candidate in candidates:
            executable = candidate if candidate.is_file() else candidate / "steam.exe"
            if executable.is_file():
                return executable
        return None

    def _steam_running(self) -> bool:
        expected = self._steam_executable()
        if not expected:
            return False
        normalized = self._normalize_path(expected)
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if (proc.info.get("name") or "").casefold() == expected.name.casefold():
                    actual = proc.info.get("exe") or ""
                    if actual and self._normalize_path(actual) == normalized:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return False

    @staticmethod
    def _account_selected(steam_root: Path | None) -> bool:
        if not steam_root:
            return False
        loginusers = steam_root / "config" / "loginusers.vdf"
        try:
            content = loginusers.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        account_blocks = re.findall(r'"[0-9]{10,20}"\s*\{([^{}]*)\}', content, re.DOTALL)
        for block in account_blocks:
            auto_login = re.search(r'"AutoLogin"\s*"1"', block, re.IGNORECASE)
            remembered = re.search(r'"RememberPassword"\s*"1"', block, re.IGNORECASE)
            recent = re.search(r'"MostRecent"\s*"1"', block, re.IGNORECASE)
            if (auto_login and remembered) or recent:
                return True
        return False

    def _library_roots(self, steam_root: Path | None) -> list[Path]:
        roots: list[Path] = []
        if steam_root:
            roots.append(steam_root)
        if self.config.steam_library_path:
            configured = Path(self.config.steam_library_path).expanduser()
            if configured not in roots:
                roots.append(configured)
        if not steam_root:
            return roots
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        try:
            content = library_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return roots
        for raw_path in re.findall(r'"path"\s*"([^"]+)"', content):
            library = Path(raw_path.replace("\\\\", "\\"))
            if library not in roots:
                roots.append(library)
        return roots

    def _install_root(self, libraries: list[Path]) -> Path | None:
        if self._cached_root and self._cached_root.is_dir():
            return self._cached_root
        for library in libraries:
            manifest = library / "steamapps" / self.game.manifest_name
            try:
                content = manifest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = re.search(r'"installdir"\s*"([^"]+)"', content, re.IGNORECASE)
            if match:
                root = library / "steamapps" / "common" / match.group(1)
                if root.is_dir():
                    self._cached_root = root
                    return root
        if self.config.assetto_corsa_path:
            configured = Path(self.config.assetto_corsa_path).expanduser()
            if configured.is_dir():
                self._cached_root = configured
                return configured
        return None

    def _game_executable(self, install_root: Path | None) -> Path | None:
        if not install_root:
            return None
        if self._cached_executable and self._cached_executable.is_file():
            return self._cached_executable
        direct = install_root / self.game.executable_name
        if direct.is_file():
            self._cached_executable = direct
            return direct
        try:
            for candidate in install_root.rglob(self.game.executable_name):
                if candidate.is_file():
                    self._cached_executable = candidate
                    return candidate
        except OSError:
            return None
        return None

    @staticmethod
    def _post_close_to_windows(pids: set[int]) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True

        user32.EnumWindows(callback_type(visit), 0)
