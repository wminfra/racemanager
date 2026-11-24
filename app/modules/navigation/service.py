from __future__ import annotations

import collections
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.models import AppSetting, AuditLog
from app.modules.navigation.constants import ACTIONS, ALLOWED_ACTIONS
from app.modules.navigation.device_manager import DeviceManager, JoystickDevice
from app.modules.navigation.keyboard_output import KeyboardOutputInterface, get_default_keyboard_output
from app.modules.navigation.mapping import NavigationConfig

logger = logging.getLogger(__name__)

SETTING_KEY = "navigation_controls_config"


class NavigationService:
    """Coordinates joystick listening, button debouncing, repeat dispatching, and keyboard output."""

    def __init__(
        self,
        session_factory: Callable[..., Any] | None = None,
        device_manager: DeviceManager | None = None,
        keyboard_output: KeyboardOutputInterface | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.device_manager = device_manager or DeviceManager()
        self.keyboard_output = keyboard_output or get_default_keyboard_output()

        self._lock = threading.RLock()
        self._config = NavigationConfig.default()
        self._connected = False
        self._active_device: JoystickDevice | None = None

        # Per-button state tracking: button_id -> timestamp / bool
        self._btn_pressed: dict[int, bool] = {}
        self._btn_press_start: dict[int, float] = {}
        self._btn_last_press: dict[int, float] = {}
        self._btn_last_release: dict[int, float] = {}
        self._btn_last_dispatch: dict[int, float] = {}

        # Detection mode support
        self._detect_event = threading.Event()
        self._detecting_action: str | None = None
        self._detected_button: int | None = None

        # Recent events history for live UI feedback
        self._recent_events: collections.deque[dict[str, Any]] = collections.deque(maxlen=20)
        self._last_event: dict[str, Any] | None = None

        # Worker thread
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def config(self) -> NavigationConfig:
        with self._lock:
            return self._config

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def load_config(self) -> NavigationConfig:
        """Load stored navigation configuration from the database or use defaults."""
        if not self.session_factory:
            return self.config

        with self.session_factory() as db:
            setting = db.get(AppSetting, SETTING_KEY)
            if setting and setting.value:
                try:
                    data = json.loads(setting.value)
                    with self._lock:
                        self._config = NavigationConfig.from_dict(data)
                        self._resolve_device_locked()
                    logger.info("[NAV-SERVICE] Configuração de navegação carregada do banco de dados.")
                    return self._config
                except Exception as exc:
                    logger.warning("[NAV-SERVICE] Falha ao ler configuração salva: %s. Usando padrões.", exc)

            # Auto-detect initial device if an Arduino joystick is currently connected
            with self._lock:
                self._auto_detect_initial_device_locked()
                # Persist default
                if setting is None:
                    db.add(AppSetting(key=SETTING_KEY, value=json.dumps(self._config.to_dict())))
                else:
                    setting.value = json.dumps(self._config.to_dict())
                db.commit()
            return self._config

    def save_config(self, new_config: NavigationConfig, actor_id: int | None = None) -> NavigationConfig:
        """Persist updated navigation configuration to the database and apply immediately."""
        with self._lock:
            self._config = new_config
            self._resolve_device_locked()

        if self.session_factory:
            with self.session_factory() as db:
                setting = db.get(AppSetting, SETTING_KEY)
                if setting is None:
                    setting = AppSetting(key=SETTING_KEY, value=json.dumps(new_config.to_dict()))
                    db.add(setting)
                else:
                    setting.value = json.dumps(new_config.to_dict())
                if actor_id:
                    db.add(
                        AuditLog(
                            actor_id=actor_id,
                            action="navigation.config_updated",
                            details=f"device={new_config.device_name} enabled={new_config.enabled}",
                        )
                    )
                db.commit()
        logger.info("[NAV-SERVICE] Configuração salva e atualizada em memória.")
        return self._config

    def restore_defaults(self, actor_id: int | None = None) -> NavigationConfig:
        """Reset mappings and parameters to default values and persist."""
        with self._lock:
            defaults = NavigationConfig.default()
            # Retain current device if already selected, or auto-detect
            if self._config.device_identifier:
                defaults.device_identifier = self._config.device_identifier
                defaults.device_name = self._config.device_name
                defaults.device_index = self._config.device_index
            else:
                self._auto_detect_initial_device_locked()
                defaults.device_identifier = self._config.device_identifier
                defaults.device_name = self._config.device_name
                defaults.device_index = self._config.device_index
            return self.save_config(defaults, actor_id=actor_id)

    def _auto_detect_initial_device_locked(self) -> None:
        devices = self.device_manager.list_devices()
        arduino_dev = None
        for dev in devices:
            if "arduino" in dev.name.lower() or dev.hardware_id.upper() in {"VID_2341&PID_8037", "VID_2341&PID_8036"}:
                arduino_dev = dev
                break
        chosen = arduino_dev or (devices[0] if devices else None)
        if chosen:
            self._config.device_identifier = chosen.hardware_id
            self._config.device_name = chosen.name
            self._config.device_index = chosen.index
            self._active_device = chosen
            logger.info("[NAV-SERVICE] Dispositivo detectado automaticamente: %s (%s)", chosen.name, chosen.hardware_id)

    def _resolve_device_locked(self) -> None:
        """Re-evaluate the active device index based on the configured identifier."""
        if not self._config.device_identifier and self._config.device_index is None:
            self._active_device = None
            self._connected = False
            return

        device = self.device_manager.get_device(self._config.device_identifier or self._config.device_index)
        if device:
            self._active_device = device
            self._config.device_index = device.index
            self._config.device_name = self._config.device_name or device.name
            self._connected = device.connected
        else:
            self._active_device = None
            self._connected = False

    def start(self) -> None:
        """Start the background joystick polling loop."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, name="NavigationServiceListener", daemon=True)
            self._thread.start()
        logger.info("[NAV-SERVICE] Listener de botões de navegação iniciado.")

    def stop(self) -> None:
        """Stop the background listener thread safely."""
        with self._lock:
            self._running = False
            self.cancel_detection()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("[NAV-SERVICE] Listener de botões de navegação finalizado.")

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception:
                logger.exception("[NAV-SERVICE] Erro no tick do listener de navegação")
            time.sleep(0.02)  # ~50 Hz polling

    def tick(self) -> None:
        """Single polling tick evaluating hardware buttons, debounce, repeat, and dispatch."""
        with self._lock:
            cfg = self._config
            dev_idx = cfg.device_index

        if dev_idx is None:
            # Attempt to re-resolve device every few seconds if not yet found
            with self._lock:
                self._resolve_device_locked()
            return

        now = time.monotonic()
        connected, buttons_bitmask = self.device_manager.poll_buttons(dev_idx)

        # Connection / Disconnection edge detection
        with self._lock:
            if connected != self._connected:
                self._connected = connected
                if connected:
                    logger.info("[NAV-SERVICE] Dispositivo reconectado: índice %s (%s)", dev_idx, cfg.device_name)
                else:
                    logger.warning("[NAV-SERVICE] Dispositivo desconectado: índice %s (%s)", dev_idx, cfg.device_name)

        if not connected:
            return

        btn_map = cfg.get_button_to_action_map()
        debounce_sec = cfg.debounce_ms / 1000.0
        repeat_delay_sec = cfg.repeat_delay_ms / 1000.0
        repeat_interval_sec = cfg.repeat_interval_ms / 1000.0

        # Scan buttons 1 through 32
        for button in range(1, 33):
            is_down = bool(buttons_bitmask & (1 << (button - 1)))
            was_down = self._btn_pressed.get(button, False)

            if is_down and not was_down:
                # Button Down Transition
                last_release = self._btn_last_release.get(button, 0.0)
                if (now - last_release) < debounce_sec:
                    # Debounce guard: release was too recent
                    continue

                self._btn_pressed[button] = True
                self._btn_press_start[button] = now
                self._btn_last_press[button] = now

                # Button detection mode check
                with self._lock:
                    if self._detecting_action is not None:
                        self._detected_button = button
                        self._detect_event.set()
                        logger.info("[NAV-SERVICE] Botão %s detectado para ação %s", button, self._detecting_action)
                        self._detecting_action = None

                # Normal command dispatch
                if cfg.enabled:
                    action = btn_map.get(button)
                    if action:
                        self._dispatch_action(action, button)
                        self._btn_last_dispatch[button] = now
                    else:
                        self._record_event(
                            button=button,
                            action=None,
                            command=None,
                            status="Botão não configurado",
                        )

            elif is_down and was_down:
                # Button Held (Repeat check for directional keys)
                if cfg.enabled:
                    action = btn_map.get(button)
                    if action and ACTIONS[action].can_repeat:
                        press_start = self._btn_press_start.get(button, now)
                        last_disp = self._btn_last_dispatch.get(button, now)
                        if (now - press_start) >= repeat_delay_sec and (now - last_disp) >= repeat_interval_sec:
                            self._dispatch_action(action, button)
                            self._btn_last_dispatch[button] = now

            elif not is_down and was_down:
                # Button Up Transition
                self._btn_pressed[button] = False
                self._btn_last_release[button] = now

    def _dispatch_action(self, action: str, button: int) -> bool:
        info = ACTIONS[action]
        logger.debug("[NAV-SERVICE] Disparando ação %s (Botão %s)", action, button)
        success = False
        try:
            success = self.keyboard_output.send_action(action)
        except Exception as exc:
            logger.error("[NAV-SERVICE] Falha ao enviar tecla para ação %s: %s", action, exc)

        status_text = "Comando enviado" if success else "Falha no envio de comando"
        self._record_event(
            button=button,
            action=action,
            command=info.command_name,
            status=status_text,
        )
        return success

    def _record_event(
        self,
        button: int,
        action: str | None,
        command: str | None,
        status: str,
    ) -> None:
        event = {
            "button": button,
            "action": action,
            "command": command,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._last_event = event
            self._recent_events.append(event)

    def detect_button(self, action: str | None = None, timeout_seconds: float = 10.0) -> int | None:
        """Enter detection mode and wait for the user to press a physical button."""
        with self._lock:
            self._detecting_action = action or "CAPTURE"
            self._detected_button = None
            self._detect_event.clear()

        signaled = self._detect_event.wait(timeout=max(0.5, timeout_seconds))
        with self._lock:
            self._detecting_action = None
            return self._detected_button if signaled else None

    def cancel_detection(self) -> None:
        with self._lock:
            self._detecting_action = None
            self._detect_event.set()

    def test_command(self, action: str) -> dict[str, Any]:
        """Manually trigger a navigation action from the UI to test keyboard simulation."""
        if action not in ACTIONS:
            raise ValueError(f"Ação não permitida: {action!r}")
        info = ACTIONS[action]
        success = self.keyboard_output.send_action(action)
        status_text = "Comando de teste enviado" if success else "Falha no envio do comando de teste"
        self._record_event(
            button=0,
            action=action,
            command=info.command_name,
            status=status_text,
        )
        return {
            "action": action,
            "command": info.command_name,
            "success": success,
            "status": status_text,
        }

    def get_status(self) -> dict[str, Any]:
        """Return comprehensive status for operator and admin dashboards."""
        with self._lock:
            cfg = self._config
            connected = self._connected
            dev_selected = bool(cfg.device_identifier or cfg.device_index is not None)

            if not cfg.enabled:
                system_status = "disabled"
            elif not dev_selected:
                system_status = "no_device_selected"
            elif not connected:
                system_status = "disconnected"
            else:
                system_status = "available"

            actions_count = sum(1 for btn in cfg.mappings.values() if btn is not None)

            return {
                "enabled": cfg.enabled,
                "device_selected": dev_selected,
                "device_name": cfg.device_name,
                "device_identifier": cfg.device_identifier,
                "device_index": cfg.device_index,
                "connected": connected,
                "system_status": system_status,
                "mappings": dict(cfg.mappings),
                "actions_count": actions_count,
                "debounce_ms": cfg.debounce_ms,
                "repeat_delay_ms": cfg.repeat_delay_ms,
                "repeat_interval_ms": cfg.repeat_interval_ms,
                "last_event": self._last_event,
                "recent_events": list(self._recent_events),
            }
