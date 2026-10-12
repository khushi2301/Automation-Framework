from pydantic import BaseModel
from datetime import datetime


class RunSummary(BaseModel):
    run_id: str
    suite_name: str
    application: str
    environment: str
    start_time: datetime
    end_time: datetime
    total_duration_ms: float

    total_test_cases: int
    total_steps: int
    passed_test_cases: int
    failed_test_cases: int
    blocked_test_cases: int
    skipped_test_cases: int

    passed_steps: int
    failed_steps: int
    blocked_steps: int
    skipped_steps: int

    total_tokens_used: int
    total_llm_calls: int
    cache_hit_count: int
    fallback_hit_count: int
    ai_identification_count: int
    healed_count: int

    cache_hit_rate: float
    false_heal_rate: float

    total_cost_usd: float
    cost_per_100_steps: float

    class Config:
        json_schema_extra = {
            "example": {
                "run_id": "run-001",
                "suite_name": "Automation Exercise Suite",
                "application": "automation_exercise",
                "environment": "production",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-01T01:00:00",
                "total_duration_ms": 3600000,
                "total_test_cases": 10,
                "total_steps": 50,
                "passed_test_cases": 9,
                "failed_test_cases": 1,
                "blocked_test_cases": 0,
                "skipped_test_cases": 0,
                "passed_steps": 48,
                "failed_steps": 2,
                "blocked_steps": 0,
                "skipped_steps": 0,
                "total_tokens_used": 5000,
                "total_llm_calls": 12,
                "cache_hit_count": 38,
                "fallback_hit_count": 2,
                "ai_identification_count": 10,
                "healed_count": 0,
                "cache_hit_rate": 0.76,
                "false_heal_rate": 0.0,
                "total_cost_usd": 0.15,
                "cost_per_100_steps": 0.30
            }
        }
