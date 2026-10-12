from typing import Optional, Literal
from pydantic import BaseModel
from datetime import datetime


class StepResult(BaseModel):
    step_no: int
    status: Literal["Pass", "Fail", "Blocked", "Skipped"]
    action: Optional[str] = None
    target: Optional[str] = None
    actual_result: Optional[str] = None
    expected_result: str = ""
    screenshot_path: Optional[str] = None
    execution_time_ms: float = 0.0
    element_source: Literal["Cache", "Fallback", "AI", "Healed", "None"] = "None"
    tokens_used: int = 0
    error_message: Optional[str] = None
    executed_at: datetime = None

    def __init__(self, **data):
        if data.get('executed_at') is None:
            data['executed_at'] = datetime.now()
        super().__init__(**data)


class ExecutionResult(BaseModel):
    run_id: str
    test_case_id: str
    test_case_title: str
    application: str
    environment: str
    status: Literal["Pass", "Fail", "Blocked", "Skipped"]
    actual_result: str = ""
    screenshot_path: Optional[str] = None
    steps: list[StepResult]
    total_execution_time_ms: float
    total_tokens_used: int = 0
    element_sources: dict = {}
    row_index: Optional[int] = None
    started_at: datetime
    completed_at: datetime
