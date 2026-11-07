from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    # Whether 1/2-minute sessions are allowed is a server-side test-mode rule
    # enforced by the session service, not a request-schema decision.
    duration_minutes: int = Field(ge=1, le=240)
    simulator_id: int = Field(default=1, gt=0)
    game_id: int = Field(default=1, gt=0)


class SessionDurationsUpdate(BaseModel):
    durations: list[int] = Field(min_length=1, max_length=12)
