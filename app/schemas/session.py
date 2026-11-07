from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SessionCreate(BaseModel):
    # Whether 1/2-minute sessions are allowed is a server-side test-mode rule
    # enforced by the session service, not a request-schema decision.
    duration_minutes: int = Field(ge=1, le=240)
    simulator_id: int = Field(default=1, gt=0)
    game_id: int = Field(default=1, gt=0)


class SessionDurationsUpdate(BaseModel):
    durations: list[int] = Field(min_length=1, max_length=12)


class SessionInterrupt(BaseModel):
    reason: Literal[
        "Problema no volante", "Problema nos pedais", "Problema no câmbio",
        "Problema no computador", "Problema no jogo", "Solicitação do cliente",
        "Necessidade operacional", "Outro",
    ]
    observation: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.reason == "Outro" and not (self.observation or "").strip():
            raise ValueError("Informe uma observação quando o motivo for Outro.")
        if self.reason != "Outro" and self.observation and self.observation.strip():
            raise ValueError("A observação só pode ser preenchida para o motivo Outro.")
        return self
