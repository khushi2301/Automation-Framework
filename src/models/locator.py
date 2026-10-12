from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class LocatorFingerprint(BaseModel):
    tag: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    placeholder: Optional[str] = None
    aria_label: Optional[str] = None
    href: Optional[str] = None
    context: Optional[str] = None
    class_names: Optional[List[str]] = None
    data_testid: Optional[str] = None
    position: Optional[Dict[str, float]] = None


class Locator(BaseModel):
    id: str
    application: str
    environment: str
    page: str
    element_description: str
    primary_locator: str
    locator_strategy: str
    fallback_locators: List[Dict[str, str]]
    fingerprint: LocatorFingerprint
    created_at: datetime
    updated_at: datetime
    version: int
    is_active: bool = True
    # approved: trusted reference; pending: healed, awaiting review; rejected / superseded: kept for history only
    review_status: str = "approved"
    replaces_id: Optional[str] = None


class LocatorHistory(BaseModel):
    id: str
    locator_id: str
    old_fingerprint: LocatorFingerprint
    new_fingerprint: LocatorFingerprint
    reason: str
    healed_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    decision: Optional[str] = None
