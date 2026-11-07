from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    duration_minutes: int = Field(ge=5, le=240)
    simulator_id: int = Field(default=1, gt=0)
    game_id: int = Field(default=1, gt=0)


class SessionDurationsUpdate(BaseModel):
    durations: list[int] = Field(min_length=1, max_length=12)
