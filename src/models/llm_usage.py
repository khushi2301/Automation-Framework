from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal


class LLMUsage(BaseModel):
    id: str
    run_id: str
    test_case_id: str
    step_no: int
    user_id: str
    agent_name: str
    reason: Literal["first_learning", "healing", "verification"]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    status: Literal["success", "error"]
    error_message: Optional[str] = None
    timestamp: datetime

    class Config:
        json_schema_extra = {
            "example": {
                "id": "usage-001",
                "run_id": "run-001",
                "test_case_id": "TC-001",
                "step_no": 1,
                "user_id": "user123",
                "agent_name": "LocatorAgent",
                "reason": "first_learning",
                "model": "gpt-5",
                "input_tokens": 1000,
                "output_tokens": 200,
                "latency_ms": 2500,
                "cost_usd": 0.003,
                "status": "success",
                "error_message": None,
                "timestamp": "2024-01-01T00:00:00"
            }
        }
