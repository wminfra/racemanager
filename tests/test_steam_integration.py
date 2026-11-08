from pathlib import Path

import psutil
import pytest

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


@pytest.mark.parametrize("mode,expected_args", [
    ("monitor", ["-nohmd"]),
    ("oculus", ["-vr", "hmd=OculusHMD"]),
    ("steamvr", ["-vr", "hmd=SteamVR"]),
])
def test_launch_passes_explicit_mode_to_steam(tmp_path, monkeypatch, mode, expected_args):
    integration, _ = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)
    monkeypatch.setattr(integration, "find_game_processes", lambda: [])
    calls = []
    monkeypatch.setattr("app.modules.steam.integration.subprocess.Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    integration.launch_game(mode)
    assert calls == [([str(integration._steam_executable()), "-applaunch", "805550", *expected_args], {"close_fds": True})]


def test_launch_rejects_unknown_mode_before_steam_request(tmp_path, monkeypatch):
    integration, _ = prepared_install(tmp_path)
    calls = []
    monkeypatch.setattr("app.modules.steam.integration.subprocess.Popen", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="Modo de inicialização não suportado"):
        integration.launch_game("old-steam-choice")
    assert calls == []


def test_process_detection_requires_exact_executable_path(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    monkeypatch.setattr(integration, "_steam_running", lambda: True)

    class Proc:
        def __init__(self, pid, name, exe):
            self.info = {"pid": pid, "name": name, "exe": str(exe) if exe is not None else None}

    unrelated_same_name = expected_exe.parent.parent / "other" / expected_exe.name
    processes = [Proc(51, expected_exe.name, expected_exe),
                 Proc(52, expected_exe.name, unrelated_same_name),
                 Proc(53, "steam.exe", "C:/Steam/steam.exe"),
                 Proc(54, expected_exe.name, None)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(processes))
    found = integration.find_game_processes()
    assert [item.pid for item in found] == [51, 54]
    assert found[1].executable == ""


def test_graceful_close_uses_wm_close_and_never_forces(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    entry = GameProcess(61, expected_exe.name, str(expected_exe))
    alive = {61: True}
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry] if alive[61] else [])
    requests = []
    def post_close(pids):
        requests.append(pids)
        alive[61] = False
        return 1
    monkeypatch.setattr(integration, "_post_close_to_windows", post_close)

    class ProcessStub:
        pid = 61
        def __init__(self, pid):
            if not alive[61]: raise psutil.NoSuchProcess(pid)
            assert pid == 61
        def name(self): return expected_exe.name
        def exe(self): return str(expected_exe)
        def terminate(self): raise AssertionError("automatic terminate is forbidden")
        def kill(self): raise AssertionError("automatic kill is forbidden")

    monkeypatch.setattr(psutil, "Process", ProcessStub)
    integration.shutdown.first_wait_seconds = 0
    integration.shutdown.additional_wait_seconds = 0
    state = integration.close_game(origin="test")
    assert state["state"] == "CLOSED"
    assert state["method"] == "wm_close"
    assert requests == [{61}]


def test_delayed_close_uses_one_initial_and_one_additional_wait(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    entry = GameProcess(62, expected_exe.name, str(expected_exe))
    alive = {62: True}
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry] if alive[62] else [])
    requests = []
    monkeypatch.setattr(integration, "_post_close_to_windows", lambda pids: requests.append(pids) or 1)

    class ProcessStub:
        pid = 62
        def __init__(self, pid):
            if not alive[62]: raise psutil.NoSuchProcess(pid)
            assert pid == 62
        def name(self): return expected_exe.name
        def exe(self): return str(expected_exe)
        def terminate(self): raise AssertionError("automatic terminate is forbidden")
        def kill(self): raise AssertionError("automatic kill is forbidden")

    monkeypatch.setattr(psutil, "Process", ProcessStub)
    integration.shutdown.first_wait_seconds = 0
    integration.shutdown.additional_wait_seconds = 0
    original_wait = integration.shutdown._wait_targets_exit
    wait_calls = []
    def delayed_wait(targets, timeout):
        wait_calls.append(timeout)
        if len(wait_calls) == 2:
            alive[62] = False
        return original_wait(targets, timeout)
    monkeypatch.setattr(integration.shutdown, "_wait_targets_exit", delayed_wait)
    state = integration.close_game(origin="delayed_test")
    assert state["state"] == "CLOSED"
    assert state["result"] == "closed_within_additional_wait"
    assert len(wait_calls) == 2
    assert requests == [{62}]


def test_ignored_wm_close_fails_without_forcing_and_requires_confirmation(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    entry = GameProcess(71, expected_exe.name, str(expected_exe))
    alive = {71: True}
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry] if alive[71] else [])
    monkeypatch.setattr(integration, "_post_close_to_windows", lambda _pids: 1)

    class ProcessStub:
        pid = 71
        terminated = False
        killed = False
        def __init__(self, pid): assert pid == 71
        def name(self): return expected_exe.name
        def exe(self): return str(expected_exe)
        def terminate(self): self.terminated = True
        def kill(self): self.killed = True; alive[71] = False

    process = ProcessStub(71)
    def process_for_pid(pid):
        if not alive[71]: raise psutil.NoSuchProcess(pid)
        return process
    monkeypatch.setattr(psutil, "Process", process_for_pid)
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: ([], procs))
    integration.shutdown.first_wait_seconds = 0
    integration.shutdown.additional_wait_seconds = 0
    from app.modules.steam.shutdown import GameCloseError
    import pytest
    with pytest.raises(GameCloseError):
        integration.close_game(origin="manual_test")
    assert process.terminated is False and process.killed is False
    assert integration.close_status()["state"] == "CLOSE_FAILED"
    with pytest.raises(ValueError):
        integration.force_close_game(origin="test", confirmed=False)
    state = integration.force_close_game(origin="test", confirmed=True)
    assert state["state"] == "CLOSED"
    assert process.terminated is True and process.killed is True


def test_graceful_close_can_be_retried_once_without_repeating_wm_close_indefinitely(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    entry = GameProcess(81, expected_exe.name, str(expected_exe))
    alive = {81: True}
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry] if alive[81] else [])
    calls = []
    def post_close(_pids):
        calls.append(1)
        if len(calls) == 2:
            alive[81] = False
        return 1
    monkeypatch.setattr(integration, "_post_close_to_windows", post_close)

    class ProcessStub:
        pid = 81
        def __init__(self, pid):
            if not alive[81]: raise psutil.NoSuchProcess(pid)
        def name(self): return expected_exe.name
        def exe(self): return str(expected_exe)
        def terminate(self): raise AssertionError("retry must remain graceful")
        def kill(self): raise AssertionError("retry must remain graceful")

    monkeypatch.setattr(psutil, "Process", ProcessStub)
    integration.shutdown.first_wait_seconds = 0
    integration.shutdown.additional_wait_seconds = 0
    from app.modules.steam.shutdown import GameCloseError
    import pytest
    with pytest.raises(GameCloseError): integration.close_game(origin="test")
    with pytest.raises(GameCloseError): integration.close_game(origin="supervisor")
    state = integration.close_game(origin="operator_retry", retry=True)
    assert state["state"] == "CLOSED"
    assert len(calls) == 2


def test_two_simultaneous_close_requests_send_only_one_window_message(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    integration, expected_exe = prepared_install(tmp_path)
    entry = GameProcess(82, expected_exe.name, str(expected_exe))
    alive = {82: True}
    monkeypatch.setattr(integration, "find_game_processes", lambda: [entry] if alive[82] else [])
    calls = []
    def post_close(_pids):
        calls.append(1)
        time.sleep(0.05)
        alive[82] = False
        return 1
    monkeypatch.setattr(integration, "_post_close_to_windows", post_close)

    class ProcessStub:
        pid = 82
        def __init__(self, pid):
            if not alive[82]: raise psutil.NoSuchProcess(pid)
        def name(self): return expected_exe.name
        def exe(self): return str(expected_exe)
        def terminate(self): raise AssertionError("automatic terminate is forbidden")
        def kill(self): raise AssertionError("automatic kill is forbidden")

    monkeypatch.setattr(psutil, "Process", ProcessStub)
    integration.shutdown.first_wait_seconds = 0
    integration.shutdown.additional_wait_seconds = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: integration.close_game(origin="concurrent"), range(2)))
    assert len(calls) == 1
    assert all(item["state"] == "CLOSED" for item in results)


def test_close_refuses_to_signal_process_when_path_cannot_be_verified(tmp_path, monkeypatch):
    integration, expected_exe = prepared_install(tmp_path)
    unknown = GameProcess(91, expected_exe.name, "")
    monkeypatch.setattr(integration, "find_game_processes", lambda: [unknown])
    requests = []
    monkeypatch.setattr(integration, "_post_close_to_windows", lambda pids: requests.append(pids) or 1)
    from app.modules.steam.shutdown import GameCloseError
    import pytest
    with pytest.raises(GameCloseError) as error:
        integration.close_game(origin="test")
    assert "validar o caminho" in str(error.value)
    assert integration.close_status()["state"] == "CLOSE_FAILED"
    assert requests == []
