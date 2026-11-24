from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from app.modules.navigation.constants import (
    ACTIONS,
    ALLOWED_ACTIONS,
    DEFAULT_DEBOUNCE_MS,
    DEFAULT_MAPPINGS,
    DEFAULT_REPEAT_DELAY_MS,
    DEFAULT_REPEAT_INTERVAL_MS,
)


@dataclass
class NavigationConfig:
    enabled: bool = True
    device_identifier: str | None = None
    device_name: str | None = None
    device_index: int | None = None
    mappings: dict[str, int | None] = field(default_factory=lambda: copy.deepcopy(DEFAULT_MAPPINGS))
    debounce_ms: int = DEFAULT_DEBOUNCE_MS
    repeat_delay_ms: int = DEFAULT_REPEAT_DELAY_MS
    repeat_interval_ms: int = DEFAULT_REPEAT_INTERVAL_MS

    def __post_init__(self) -> None:
        self.mappings = self.validate_mappings(self.mappings)
        self.debounce_ms = max(50, min(1000, int(self.debounce_ms)))
        self.repeat_delay_ms = max(100, min(2000, int(self.repeat_delay_ms)))
        self.repeat_interval_ms = max(50, min(500, int(self.repeat_interval_ms)))

    @staticmethod
    def validate_mappings(raw_mappings: dict[str, Any]) -> dict[str, int | None]:
        clean_mappings: dict[str, int | None] = {action: None for action in ALLOWED_ACTIONS}
        used_buttons: dict[int, str] = {}

        for action, button_val in raw_mappings.items():
            if action not in ACTIONS:
                raise ValueError(f"Ação de navegação inválida: {action!r}. Permitidas: {ALLOWED_ACTIONS}")

            if button_val is None or button_val == "" or button_val == 0:
                clean_mappings[action] = None
                continue

            try:
                button = int(button_val)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Valor de botão inválido para {action}: {button_val!r}") from exc

            if button < 1 or button > 64:
                raise ValueError(f"Número de botão inválido: {button}. Deve estar entre 1 e 64.")

            if button in used_buttons:
                prior_action = used_buttons[button]
                raise ValueError(
                    f"O botão físico {button} já está configurado para a ação '{prior_action}'. "
                    f"Cada botão deve ser dedicado a apenas uma ação."
                )

            used_buttons[button] = action
            clean_mappings[action] = button

        return clean_mappings

    def get_button_to_action_map(self) -> dict[int, str]:
        """Returns a reverse lookup map: button_number -> action_name."""
        return {btn: act for act, btn in self.mappings.items() if btn is not None}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NavigationConfig:
        mappings = data.get("mappings") or copy.deepcopy(DEFAULT_MAPPINGS)
        return cls(
            enabled=bool(data.get("enabled", True)),
            device_identifier=data.get("device_identifier"),
            device_name=data.get("device_name"),
            device_index=data.get("device_index"),
            mappings=mappings,
            debounce_ms=data.get("debounce_ms", DEFAULT_DEBOUNCE_MS),
            repeat_delay_ms=data.get("repeat_delay_ms", DEFAULT_REPEAT_DELAY_MS),
            repeat_interval_ms=data.get("repeat_interval_ms", DEFAULT_REPEAT_INTERVAL_MS),
        )

    @classmethod
    def default(cls) -> NavigationConfig:
        return cls()
