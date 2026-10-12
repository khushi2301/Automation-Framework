from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class TestStep(BaseModel):
    """One atomic browser action produced by the planner from the manual test steps."""
    step_no: int
    action: str
    target: Optional[str] = None
    value: Optional[str] = None
    source_step: Optional[int] = None
    expected_result: str = ""
    execute: bool = True

    # Kept so older code paths and reports that read these names still work.
    @property
    def element_description(self) -> Optional[str]:
        return self.target

    @property
    def test_data(self) -> Optional[str]:
        return self.value


class TestCase(BaseModel):
    """One row of the Excel suite: manual steps plus data and expectation, and the planned actions."""
    test_case_id: str
    test_case_title: str
    application: str
    environment: str
    source_steps: List[str] = Field(default_factory=list)
    test_data: Optional[str] = None
    expected_result: str = ""
    execute: bool = True
    row_index: Optional[int] = None
    steps: List[TestStep] = Field(default_factory=list)
    checks: List[Dict] = Field(default_factory=list)
