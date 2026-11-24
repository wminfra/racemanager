import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.migrations import init_navigation_config
from app.database.session import Base
from app.main import app
from app.models import AppSetting, AuditLog, User
from app.modules.navigation.constants import ACTIONS, ALLOWED_ACTIONS, DEFAULT_MAPPINGS
from app.modules.navigation.device_manager import DeviceManager, JoystickDevice
from app.modules.navigation.keyboard_output import MockKeyboardOutput
from app.modules.navigation.mapping import NavigationConfig
from app.modules.navigation.service import NavigationService
from app.services.auth import hash_password


class FakeDeviceManager:
    """Simulates joystick enumeration and button states for unit testing."""

    def __init__(self, connected: bool = True):
        self.connected = connected
        self.current_buttons = 0
        self.devices = [
            JoystickDevice(
                id="joy_0_2341_8037",
                index=0,
                name="Arduino Micro",
                hardware_id="VID_2341&PID_8037",
                vendor_id=0x2341,
                product_id=0x8037,
                num_buttons=18,
                connected=connected,
            )
        ]

    def list_devices(self) -> list[JoystickDevice]:
        return [
            JoystickDevice(
                id=d.id,
                index=d.index,
                name=d.name,
                hardware_id=d.hardware_id,
                vendor_id=d.vendor_id,
                product_id=d.product_id,
                num_buttons=d.num_buttons,
                connected=self.connected,
            )
            for d in self.devices
        ]

    def get_device(self, identifier: str | int | None) -> JoystickDevice | None:
        for dev in self.list_devices():
            if identifier in (dev.id, dev.hardware_id, dev.index, str(dev.index)):
                return dev
        return None

    def is_connected(self, index: int) -> bool:
        return self.connected

    def poll_buttons(self, index: int) -> tuple[bool, int]:
        if not self.connected:
            return False, 0
        return True, self.current_buttons


@pytest.fixture
def nav_env():
    """Isolated database and NavigationService with mock hardware for testing."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fake_dm = FakeDeviceManager(connected=True)
    mock_kb = MockKeyboardOutput()

    service = NavigationService(
        session_factory=TestingSession,
        device_manager=fake_dm,
        keyboard_output=mock_kb,
    )
    with TestingSession() as db:
        init_navigation_config(db)
        # Create test users
        admin = User(username="admin_nav", password_hash=hash_password("admin_pass_1234"), role="admin")
        operator = User(username="op_nav", password_hash=hash_password("operator_pass_1234"), role="operator")
        db.add_all([admin, operator])
        db.commit()

    service.load_config()

    previous_engine = app.state.engine
    previous_factory = app.state.session_factory
    previous_nav = getattr(app.state, "navigation_service", None)

    app.state.engine = engine
    app.state.session_factory = TestingSession
    app.state.navigation_service = service

    with TestClient(app) as client:
        yield {
            "client": client,
            "session_factory": TestingSession,
            "service": service,
            "fake_dm": fake_dm,
            "mock_kb": mock_kb,
        }

    app.state.engine = previous_engine
    app.state.session_factory = previous_factory
    app.state.navigation_service = previous_nav
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_navigation_constants_and_allowed_actions():
    expected = {"UP", "DOWN", "LEFT", "RIGHT", "ENTER", "ESC", "SPACE"}
    assert set(ALLOWED_ACTIONS) == expected
    for action in expected:
        info = ACTIONS[action]
        assert info.action == action
        assert info.vk_code > 0
        assert info.scan_code > 0
    # Directionals can repeat, confirmation/cancellation keys must not repeat
    assert ACTIONS["UP"].can_repeat is True
    assert ACTIONS["DOWN"].can_repeat is True
    assert ACTIONS["ENTER"].can_repeat is False
    assert ACTIONS["ESC"].can_repeat is False


def test_navigation_mapping_validation_and_uniqueness():
    # Valid mapping
    cfg = NavigationConfig(
        enabled=True,
        mappings={"UP": 3, "DOWN": 4, "LEFT": 5, "RIGHT": 6, "ENTER": 1, "ESC": 2, "SPACE": None},
    )
    assert cfg.mappings["ENTER"] == 1
    assert cfg.mappings["SPACE"] is None
    rev = cfg.get_button_to_action_map()
    assert rev[1] == "ENTER"
    assert rev[3] == "UP"

    # Duplicate button rejection
    with pytest.raises(ValueError, match="já está configurado para a ação"):
        NavigationConfig(
            mappings={"UP": 1, "ENTER": 1}
        )

    # Invalid action rejection
    with pytest.raises(ValueError, match="Ação de navegação inválida"):
        NavigationConfig(
            mappings={"WIN_KEY": 1}
        )

    # Invalid button number
    with pytest.raises(ValueError, match="Número de botão inválido"):
        NavigationConfig(
            mappings={"UP": 999}
        )


def test_keyboard_output_security_and_dispatch():
    mock_kb = MockKeyboardOutput()
    assert mock_kb.send_action("UP") is True
    assert len(mock_kb.sent_actions) == 1
    assert mock_kb.sent_actions[0][0] == "UP"

    # Must reject any action not in ALLOWED_ACTIONS
    with pytest.raises(ValueError, match="Ação não permitida"):
        mock_kb.send_action("EXPLORER_EXE")


def test_navigation_service_button_press_and_dispatch(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # Initial state: no button pressed
    fake_dm.current_buttons = 0
    service.tick()
    assert len(mock_kb.sent_actions) == 0

    # Press Button 1 (mapped to ENTER by default)
    fake_dm.current_buttons = 1 << (1 - 1)  # Bit 0 = Button 1
    service.tick()

    assert len(mock_kb.sent_actions) == 1
    assert mock_kb.sent_actions[0][0] == "ENTER"

    status = service.get_status()
    assert status["last_event"]["action"] == "ENTER"
    assert status["last_event"]["button"] == 1
    assert status["last_event"]["status"] == "Comando enviado"


def test_navigation_service_debounce_suppresses_rapid_repress(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # First press Button 1
    fake_dm.current_buttons = 1
    service.tick()
    assert len(mock_kb.sent_actions) == 1

    # Release Button 1
    fake_dm.current_buttons = 0
    service.tick()

    # Immediately re-press Button 1 without waiting for debounce (debounce=200ms)
    fake_dm.current_buttons = 1
    service.tick()
    # Should still only be 1 sent action because of debounce
    assert len(mock_kb.sent_actions) == 1


def test_navigation_service_no_repeat_for_enter_when_held(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # Press Button 1 (ENTER)
    fake_dm.current_buttons = 1
    service.tick()
    assert len(mock_kb.sent_actions) == 1

    # Simulate holding the button down over multiple ticks (e.g. 1 second)
    service._btn_press_start[1] = time.monotonic() - 1.0
    service._btn_last_dispatch[1] = time.monotonic() - 0.5
    service.tick()
    service.tick()

    # Must still only be 1 action dispatched! (ENTER does not repeat)
    assert len(mock_kb.sent_actions) == 1


def test_navigation_service_repeat_for_directional_keys(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # Button 3 is UP
    fake_dm.current_buttons = 1 << (3 - 1)
    service.tick()
    assert len(mock_kb.sent_actions) == 1
    assert mock_kb.sent_actions[0][0] == "UP"

    # Simulate held past repeat delay (400ms) and repeat interval (150ms)
    service._btn_press_start[3] = time.monotonic() - 0.6
    service._btn_last_dispatch[3] = time.monotonic() - 0.2
    service.tick()

    # Second UP command dispatched!
    assert len(mock_kb.sent_actions) == 2
    assert mock_kb.sent_actions[1][0] == "UP"


def test_navigation_service_unmapped_button(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # Button 15 is unmapped
    fake_dm.current_buttons = 1 << (15 - 1)
    service.tick()

    # No key should be dispatched
    assert len(mock_kb.sent_actions) == 0
    status = service.get_status()
    assert status["last_event"]["button"] == 15
    assert status["last_event"]["action"] is None
    assert status["last_event"]["status"] == "Botão não configurado"


def test_navigation_service_disabled_mode(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    cfg = service.config
    cfg.enabled = False
    service.save_config(cfg)

    # Press Button 1 (ENTER)
    fake_dm.current_buttons = 1
    service.tick()

    # System is disabled, so nothing sent
    assert len(mock_kb.sent_actions) == 0
    assert service.get_status()["system_status"] == "disabled"


def test_navigation_service_disconnect_and_reconnect(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]
    mock_kb = nav_env["mock_kb"]

    # First tick: connected
    service.tick()
    assert service.is_connected is True

    # Simulate physical disconnection
    fake_dm.connected = False
    fake_dm.current_buttons = 1  # Even if buttons were high, disconnect overrides
    service.tick()
    assert service.is_connected is False
    assert service.get_status()["system_status"] == "disconnected"
    assert len(mock_kb.sent_actions) == 0

    # Simulate physical reconnection without restarting server
    fake_dm.connected = True
    fake_dm.current_buttons = 0
    service.tick()
    assert service.is_connected is True
    assert service.get_status()["system_status"] == "available"


def test_navigation_service_button_detection(nav_env):
    service = nav_env["service"]
    fake_dm = nav_env["fake_dm"]

    # Queue detection in background thread
    import threading
    detected = []

    def run_detect():
        btn = service.detect_button(action="UP", timeout_seconds=2.0)
        detected.append(btn)

    t = threading.Thread(target=run_detect)
    t.start()
    time.sleep(0.05)

    # Simulate user pressing Button 5
    fake_dm.current_buttons = 1 << (5 - 1)
    service.tick()

    t.join(timeout=2.0)
    assert detected == [5]


def test_persistence_and_database_reloading(nav_env):
    service = nav_env["service"]
    session_factory = nav_env["session_factory"]

    # Update config with custom mappings
    new_cfg = NavigationConfig(
        enabled=True,
        device_identifier="VID_2341&PID_8037",
        device_name="Arduino Micro Custom",
        device_index=0,
        mappings={"UP": 11, "DOWN": 12, "LEFT": 13, "RIGHT": 14, "ENTER": 9, "ESC": 10, "SPACE": 15},
        debounce_ms=250,
        repeat_delay_ms=500,
        repeat_interval_ms=180,
    )
    service.save_config(new_cfg)

    # Verify directly in database
    with session_factory() as db:
        row = db.get(AppSetting, "navigation_controls_config")
        assert row is not None
        saved_data = json.loads(row.value)
        assert saved_data["device_name"] == "Arduino Micro Custom"
        assert saved_data["mappings"]["UP"] == 11

    # Simulate full server restart by creating a brand new service
    new_service = NavigationService(session_factory=session_factory, device_manager=nav_env["fake_dm"])
    loaded_cfg = new_service.load_config()

    assert loaded_cfg.device_name == "Arduino Micro Custom"
    assert loaded_cfg.mappings["UP"] == 11
    assert loaded_cfg.debounce_ms == 250
    assert loaded_cfg.repeat_delay_ms == 500


def test_operator_can_view_navigation_status(nav_env):
    client = nav_env["client"]
    # Login as operator
    login_res = client.post("/api/auth/login", json={"username": "op_nav", "password": "operator_pass_1234"})
    assert login_res.status_code == 200

    res = client.get("/api/operator/navigation/status")
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is True
    assert data["connected"] is True
    assert data["system_status"] in {"available", "no_device_selected"}
    assert "mappings" in data


def test_operator_cannot_modify_admin_navigation_endpoints(nav_env):
    client = nav_env["client"]
    client.post("/api/auth/login", json={"username": "op_nav", "password": "operator_pass_1234"})

    # Operator should be rejected with 403 on admin routes
    assert client.get("/api/admin/navigation/devices").status_code == 403
    assert client.get("/api/admin/navigation/config").status_code == 403
    assert client.put("/api/admin/navigation/config", json={}).status_code == 403
    assert client.post("/api/admin/navigation/defaults").status_code == 403
    assert client.get("/api/admin/navigation/detect-button").status_code == 403
    assert client.post("/api/admin/navigation/test-command", json={"action": "UP"}).status_code == 403


def test_admin_navigation_endpoints_full_lifecycle(nav_env):
    client = nav_env["client"]
    # Login as admin
    login_res = client.post("/api/auth/login", json={"username": "admin_nav", "password": "admin_pass_1234"})
    assert login_res.status_code == 200

    # 1. List devices
    dev_res = client.get("/api/admin/navigation/devices")
    assert dev_res.status_code == 200
    devices = dev_res.json()
    assert len(devices) >= 1
    assert devices[0]["name"] == "Arduino Micro"

    # 2. Get current config
    cfg_res = client.get("/api/admin/navigation/config")
    assert cfg_res.status_code == 200
    cfg = cfg_res.json()
    assert "mappings" in cfg

    # 3. Update config with valid payload
    update_payload = {
        "enabled": True,
        "device_identifier": "VID_2341&PID_8037",
        "device_name": "Arduino Micro",
        "device_index": 0,
        "mappings": {"UP": 3, "DOWN": 4, "LEFT": 5, "RIGHT": 6, "ENTER": 1, "ESC": 2, "SPACE": 7},
        "debounce_ms": 180,
        "repeat_delay_ms": 350,
        "repeat_interval_ms": 120,
    }
    put_res = client.put("/api/admin/navigation/config", json=update_payload)
    assert put_res.status_code == 200
    assert put_res.json()["debounce_ms"] == 180
    assert put_res.json()["mappings"]["SPACE"] == 7

    # 4. Reject duplicate button mapping
    bad_payload = dict(update_payload)
    bad_payload["mappings"] = {"UP": 1, "ENTER": 1}
    bad_res = client.put("/api/admin/navigation/config", json=bad_payload)
    assert bad_res.status_code == 422
    assert "dedicado" in bad_res.json()["detail"].lower() or "configurado" in bad_res.json()["detail"].lower()

    # 5. Test manual command
    cmd_res = client.post("/api/admin/navigation/test-command", json={"action": "ENTER"})
    assert cmd_res.status_code == 200
    assert cmd_res.json()["action"] == "ENTER"
    assert cmd_res.json()["command"] == "Enter"
    assert cmd_res.json()["success"] is True

    # 6. Test events listing
    events_res = client.get("/api/admin/navigation/test-events")
    assert events_res.status_code == 200
    assert events_res.json()["last_event"]["action"] == "ENTER"

    # 7. Restore defaults
    defaults_res = client.post("/api/admin/navigation/defaults")
    assert defaults_res.status_code == 200
    assert defaults_res.json()["mappings"]["ENTER"] == DEFAULT_MAPPINGS["ENTER"]
