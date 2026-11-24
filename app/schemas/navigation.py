from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class NavigationDeviceOut(BaseModel):
    id: str
    index: int
    name: str
    hardware_id: str
    vendor_id: int
    product_id: int
    num_buttons: int
    connected: bool


class NavigationConfigIn(BaseModel):
    enabled: bool = True
    device_identifier: str | None = None
    device_name: str | None = None
    device_index: int | None = None
    mappings: dict[str, int | None] = Field(default_factory=dict)
    debounce_ms: int = Field(default=200, ge=50, le=1000)
    repeat_delay_ms: int = Field(default=400, ge=100, le=2000)
    repeat_interval_ms: int = Field(default=150, ge=50, le=500)


class NavigationConfigOut(BaseModel):
    enabled: bool
    device_identifier: str | None
    device_name: str | None
    device_index: int | None
    mappings: dict[str, int | None]
    debounce_ms: int
    repeat_delay_ms: int
    repeat_interval_ms: int


class NavigationStatusOut(BaseModel):
    enabled: bool
    device_selected: bool
    device_name: str | None
    device_identifier: str | None
    device_index: int | None
    connected: bool
    system_status: str  # "available", "disconnected", "disabled", "no_device_selected"
    mappings: dict[str, int | None]
    actions_count: int
    debounce_ms: int
    repeat_delay_ms: int
    repeat_interval_ms: int
    last_event: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class ButtonDetectResponse(BaseModel):
    detected_button: int | None
    status: str  # "detected", "timeout", "cancelled"
    action: str | None = None


class TestCommandRequest(BaseModel):
    action: str


class TestCommandResponse(BaseModel):
    action: str
    command: str
    success: bool
    status: str
