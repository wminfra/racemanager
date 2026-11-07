import os
from pathlib import Path

import psutil

from app.core.config import Settings
from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE
from app.modules.steam.integration import GameProcess, SteamIntegration


def prepared_install(tmp_path: Path) -> tuple[SteamIntegration, Path]:
    steam_root = tmp_path / "steam-client"
    library = tmp_path / "secondary-library"
    install = library / "steamapps" / "common" / "ACC install directory"
    steam_exe = steam_root / "steam.exe"
    steam_exe.parent.mkdir(parents=True)
    steam_exe.write_bytes(b"steam stub")
    loginusers = steam_root / "config" / "loginusers.vdf"
    loginusers.parent.mkdir(parents=True)
    loginusers.write_text('"users" { "76561198000000000" { "AutoLogin" "1" "RememberPassword" "1" } }', encoding="utf-8")
    manifest = library / "steamapps" / ASSETTO_CORSA_COMPETIZIONE.manifest_name
    manifest.parent.mkdir(parents=True)
    manifest.write_text('"AppState" { "appid" "805550" "installdir" "ACC install directory" }', encoding="utf-8")
    exe = install / "AC2" / "Binaries" / "Win64" / ASSETTO_CORSA_COMPETIZIONE.executable_name
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"game stub")
    config = Settings(secret_key="test-secret", steam_path=str(steam_exe), steam_library_path=str(library))
    return SteamIntegration(config), exe


def test_game_definition_has_the_configured_acc_appid_and_protocol():
    game = ASSETTO_CORSA_COMPETIZIONE
    assert game.name == "Assetto Corsa Competizione"
    assert game.app_id == 805550
    assert game.launch_uri == "steam://rungameid/805550"
    assert game.executable_name == "AC2-Win64-Shipping.exe"


def test_steam_and_acc_are_detected_from_configured_library_path(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)
    state = integration.inspect()
    assert state["steam_installed"] is True
    assert state["steam_running"] is True
    assert state["account_selected"] is True
    assert state["game_installed"] is True
    assert state["installation_accessible"] is True
    assert state["ready"] is True
    assert Path(state["game_executable"]) == expected_exe


def test_steam_must_be_running_even_when_acc_is_installed(tmp_path, monkeypatch):
    integration, _ = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: False)
    state = integration.inspect()
    assert state["game_installed"] is True
    assert state["ready"] is False
    assert "Abra a Steam" in state["reason"]


def test_launch_uses_only_the_official_rungameid_protocol(tmp_path, monkeypatch):
    integration, _ = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)
    monkeypatch.setattr(integration, "find_game_processes", lambda: [])
    calls = []
    monkeypatch.setattr(os, "startfile", calls.append, raising=False)
    integration.launch_game()
    assert calls == ["steam://rungameid/805550"]


def test_process_detection_requires_exact_executable_path(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)

    class Proc:
        def __init__(self, pid, name, exe):
            self.info = {"pid": pid, "name": name, "exe": str(exe)}

    unrelated_same_name = expected_exe.parent.parent / "other" / expected_exe.name
    processes = [Proc(51, expected_exe.name, expected_exe),
                 Proc(52, expected_exe.name, unrelated_same_name),
                 Proc(53, "steam.exe", "C:/Steam/steam.exe")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(processes))
    found = integration.find_game_processes()
    assert [item.pid for item in found] == [51]


def test_controlled_close_prefers_normal_window_close_and_validates_target(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)
    entry = GameProcess(61, expected_exe.name, str(expected_exe))
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry])
    closed = []
    monkeypatch.setattr(integration, "_post_close_to_windows", lambda pids: closed.append(pids))

    class Gone:
        def __init__(self, pid):
            raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", Gone)
    integration.close_game(grace_seconds=0)
    assert closed == [{61}]


def test_controlled_close_forces_only_the_validated_game_process(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)
    entry = GameProcess(71, expected_exe.name, str(expected_exe))
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry])
    monkeypatch.setattr(integration, "_post_close_to_windows", lambda pids: None)

    class ProcessStub:
        pid = 71
        terminated = False
        killed = False

        def __init__(self, pid):
            assert pid == 71

        def exe(self):
            return str(expected_exe)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = ProcessStub(71)
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: ([], procs))
    integration.close_game(grace_seconds=0)
    assert process.terminated is True
    assert process.killed is True
