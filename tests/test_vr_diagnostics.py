from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from app.models import User
from app.modules.vr.diagnostics import VRDiagnostics
from app.services.auth import hash_password


class FakeSteam:
    def __init__(self, running=False):
        self.running = running
        self.launch_calls = 0

    def inspect(self):
        return {"steam_installed": True, "steam_running": True, "game_installed": True}

    def find_game_processes(self):
        return [object()] if self.running else []

    def _steam_executable(self):
        return Path("C:/Steam/steam.exe")

    def _library_roots(self, root):
        return [root]


def test_diagnostic_does_not_start_runtime_or_session(monkeypatch, tmp_path):
    steam = FakeSteam()
    diag = VRDiagnostics(steam=steam)
    monkeypatch.setattr(diag, "_steamvr_root", lambda: None)
    monkeypatch.setattr(diag, "_meta_root", lambda: None)
    result = diag.inspect("steamvr")
    assert result["steamvr"]["runtime_running"] == "unknown"
    assert result["steamvr"]["hmd_connected"] == "unknown"
    assert result["steamvr"]["tracking_valid"] == "unknown"
    assert result["overall"] == "unknown"
    assert result["read_only"] is True
    assert steam.launch_calls == 0


def test_diagnostic_uses_unknown_for_unverifiable_oculus_headset(monkeypatch, tmp_path):
    diag = VRDiagnostics(steam=FakeSteam())
    monkeypatch.setattr(diag, "_meta_root", lambda: tmp_path)
    monkeypatch.setattr(diag, "_steamvr_root", lambda: None)
    (tmp_path / "Support" / "oculus-client").mkdir(parents=True)
    (tmp_path / "Support" / "oculus-runtime").mkdir(parents=True)
    (tmp_path / "Support" / "oculus-client" / "OculusClient.exe").touch()
    (tmp_path / "Support" / "oculus-runtime" / "LibOVRRT64_1.dll").touch()
    monkeypatch.setattr(diag, "_process_running", lambda *_: True)
    monkeypatch.setattr(diag, "_oculus_service_state", lambda: "available")
    result = diag.inspect("oculus")
    assert result["meta_link"]["client_running"] == "available"
    assert result["meta_link"]["runtime_service"] == "available"
    assert result["meta_link"]["headset_connected"] == "unknown"
    assert result["meta_link"]["headset_worn"] == "unknown"
    assert result["overall"] == "unknown"


def test_openvr_probe_reports_connected_hmd_and_valid_tracking(monkeypatch, tmp_path):
    steamvr = tmp_path / "SteamVR"
    (steamvr / "bin" / "win64").mkdir(parents=True)
    pose = SimpleNamespace(bPoseIsValid=True, eTrackingResult=200)

    class FakeSystem:
        def isTrackedDeviceConnected(self, index):
            return index == 0

        def getTrackedDeviceClass(self, index):
            return 1

        def getDeviceToAbsoluteTrackingPose(self, *_args):
            return [pose]

    fake_openvr = SimpleNamespace(
        VRApplication_Background=3, k_unTrackedDeviceIndex_Hmd=0,
        TrackedDeviceClass_HMD=1, TrackingUniverseStanding=1,
        k_unMaxTrackedDeviceCount=64, TrackingResult_Running_OK=200,
        TrackingResult_Uninitialized=1, TrackingResult_Calibrating_InProgress=100,
        TrackingResult_Calibrating_OutOfRange=101, TrackingResult_Running_OutOfRange=201,
        init=lambda _application: FakeSystem(), shutdown=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "openvr", fake_openvr)
    monkeypatch.setattr("app.modules.vr.diagnostics.tempfile.gettempdir", lambda: str(tmp_path))

    result = VRDiagnostics._openvr_status(steamvr, True)

    assert result["runtime_query"] == "success"
    assert result["hmd_connected"] == "available"
    assert result["hmd_class"] == "hmd"
    assert result["tracking_valid"] == "available"
    assert result["tracking_result"] == "Running_OK"


def test_invalid_vr_mode_is_rejected():
    with pytest.raises(ValueError):
        VRDiagnostics(steam=FakeSteam()).inspect("launch-steamvr")


def test_operator_vr_diagnostic_requires_operator_or_admin(client, db_session, monkeypatch):
    class FakeDiagnostics:
        def inspect(self, mode):
            return {"mode": mode, "read_only": True, "overall": "unknown"}

    monkeypatch.setattr(client.app.state, "vr_diagnostics", FakeDiagnostics())
    assert client.get("/api/operator/vr/diagnostic?mode=steamvr").status_code == 401
    db_session.add(User(username="racer", password_hash=hash_password("long-enough-password"), role="operator"))
    db_session.commit()
    assert client.post("/api/auth/login", json={"username": "racer", "password": "long-enough-password"}).status_code == 200
    result = client.get("/api/operator/vr/diagnostic?mode=steamvr")
    assert result.status_code == 200
    assert result.json()["read_only"] is True
    assert client.get("/api/operator/vr/diagnostic?mode=unknown").status_code == 422


@pytest.mark.parametrize(("runtime", "connected", "tracking", "expected"), [
    ("available", "connected", "available", "connected"),
    ("available", "disconnected", "unavailable", "disconnected"),
    ("unavailable", "unknown", "unknown", "runtime_unavailable"),
    ("available", "unknown", "unknown", "unknown"),
])
def test_meta_pcvr_state_requires_runtime_hmd_and_tracking(runtime, connected, tracking, expected):
    meta = {"runtime": runtime, "connected": connected, "tracking": tracking,
            "worn": "unknown", "source": "Meta PC SDK (LibOVR)", "detail": "simulado"}
    result = VRDiagnostics._pcvr_status("oculus", False, connected, tracking, meta)
    assert result["state"] == expected
    assert result["source"] == "Meta PC SDK (LibOVR)"


def test_oculus_probe_does_not_require_steamvr(monkeypatch, tmp_path):
    meta = {"runtime": "available", "connected": "connected", "tracking": "available",
            "worn": "available", "source": "Meta PC SDK (LibOVR)", "detail": "HMD e tracking OK"}
    result = VRDiagnostics._pcvr_status("oculus", False, "connected", "available", meta)
    assert result["state"] == "connected"


def test_meta_runtime_query_uses_sdk_only_when_runtime_library_exists(monkeypatch, tmp_path):
    diag = VRDiagnostics(steam=FakeSteam())
    root = tmp_path / "Meta Horizon"
    runtime = root / "Support" / "oculus-runtime"
    runtime.mkdir(parents=True)
    (runtime / "LibOVRRT64_1.dll").touch()
    monkeypatch.setattr(diag, "_meta_root", lambda: root)
    monkeypatch.setattr(diag, "_steamvr_root", lambda: None)
    monkeypatch.setattr(diag, "_oculus_service_state", lambda: "available")
    monkeypatch.setattr("app.modules.vr.diagnostics.query_meta_runtime", lambda _root: {
        "runtime": "available", "connected": "connected", "tracking": "available",
        "worn": "available", "source": "Meta PC SDK (LibOVR)", "detail": "tracking v?lido"})
    result = diag.inspect("oculus")
    assert result["pcvr"]["state"] == "connected"
    assert result["meta_link"]["headset_connected"] == "connected"
    assert result["pcvr"]["source"] == "Meta PC SDK (LibOVR)"
