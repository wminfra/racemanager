from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ActionInfo:
    action: str
    label: str
    command_name: str
    vk_code: int
    scan_code: int
    is_extended: bool
    can_repeat: bool


ACTIONS: Final[dict[str, ActionInfo]] = {
    "UP": ActionInfo(
        action="UP",
        label="Navegar para cima",
        command_name="Seta para cima",
        vk_code=0x26,      # VK_UP
        scan_code=0x48,
        is_extended=True,
        can_repeat=True,
    ),
    "DOWN": ActionInfo(
        action="DOWN",
        label="Navegar para baixo",
        command_name="Seta para baixo",
        vk_code=0x28,      # VK_DOWN
        scan_code=0x50,
        is_extended=True,
        can_repeat=True,
    ),
    "LEFT": ActionInfo(
        action="LEFT",
        label="Navegar para esquerda",
        command_name="Seta para esquerda",
        vk_code=0x25,      # VK_LEFT
        scan_code=0x4B,
        is_extended=True,
        can_repeat=True,
    ),
    "RIGHT": ActionInfo(
        action="RIGHT",
        label="Navegar para direita",
        command_name="Seta para direita",
        vk_code=0x27,      # VK_RIGHT
        scan_code=0x4D,
        is_extended=True,
        can_repeat=True,
    ),
    "ENTER": ActionInfo(
        action="ENTER",
        label="Confirmar / Selecionar",
        command_name="Enter",
        vk_code=0x0D,      # VK_RETURN
        scan_code=0x1C,
        is_extended=False,
        can_repeat=False,
    ),
    "ESC": ActionInfo(
        action="ESC",
        label="Voltar / Cancelar",
        command_name="Escape",
        vk_code=0x1B,      # VK_ESCAPE
        scan_code=0x01,
        is_extended=False,
        can_repeat=False,
    ),
    "SPACE": ActionInfo(
        action="SPACE",
        label="Espaço / Alternar",
        command_name="Espaço",
        vk_code=0x20,      # VK_SPACE
        scan_code=0x39,
        is_extended=False,
        can_repeat=False,
    ),
}

ALLOWED_ACTIONS: Final[tuple[str, ...]] = tuple(ACTIONS.keys())

DEFAULT_MAPPINGS: Final[dict[str, int | None]] = {
    "UP": 3,
    "DOWN": 4,
    "LEFT": 5,
    "RIGHT": 6,
    "ENTER": 1,
    "ESC": 2,
    "SPACE": None,
}

DEFAULT_DEBOUNCE_MS: Final[int] = 200
DEFAULT_REPEAT_DELAY_MS: Final[int] = 400
DEFAULT_REPEAT_INTERVAL_MS: Final[int] = 150
