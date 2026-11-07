from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    price_option_id: int = Field(gt=0)
    simulator_id: int = Field(default=1, gt=0)
