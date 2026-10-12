import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from src.models import Locator, LocatorFingerprint
from src.storage import DatabaseManager
from src.utils import get_logger


logger = get_logger(__name__)


class LocatorStoreTool:
    """Persistent, versioned element reference store keyed by application, environment, page and target description."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def get_references(self, application: str, environment: str, page: str,
                       element_description: str) -> Dict[str, Optional[Locator]]:
        return {
            'approved': self.db.get_reference(application, environment, page, element_description, 'approved'),
            'pending': self.db.get_reference(application, environment, page, element_description, 'pending'),
        }

    def create_locator(self, application: str, environment: str, page: str,
                       element_description: str, strategies: List[Dict[str, str]],
                       fingerprint_data: Dict[str, Any]) -> Locator:
        now = datetime.now()
        locator = Locator(
            id=f"loc-{uuid.uuid4().hex[:8]}",
            application=application, environment=environment, page=page,
            element_description=element_description,
            primary_locator=strategies[0]['locator'],
            locator_strategy=strategies[0]['strategy'],
            fallback_locators=strategies[1:],
            fingerprint=LocatorFingerprint(**fingerprint_data),
            created_at=now, updated_at=now, version=1, is_active=True,
            review_status='approved'
        )
        self.db.save_locator(locator)
        logger.info(f"Stored new reference {locator.id} for '{element_description}' on {page}")
        return locator

    def heal_locator(self, old: Locator, strategies: List[Dict[str, str]],
                     fingerprint_data: Dict[str, Any], auto_approve: bool) -> Locator:
        """Create the next version of a reference. The old version stays trusted until a reviewer approves the new one."""
        now = datetime.now()
        new_fp = LocatorFingerprint(**fingerprint_data)

        pending = self.db.get_reference(old.application, old.environment, old.page,
                                        old.element_description, 'pending')
        if pending:
            self.db.set_locator_state(pending.id, False, 'superseded')

        new = Locator(
            id=f"loc-{uuid.uuid4().hex[:8]}",
            application=old.application, environment=old.environment, page=old.page,
            element_description=old.element_description,
            primary_locator=strategies[0]['locator'],
            locator_strategy=strategies[0]['strategy'],
            fallback_locators=strategies[1:],
            fingerprint=new_fp, created_at=now, updated_at=now,
            version=max(old.version, pending.version if pending else 0) + 1,
            is_active=True, review_status='pending', replaces_id=old.id
        )
        self.db.save_locator(new)
        self.db.save_history(f"hist-{uuid.uuid4().hex[:8]}", new.id, old.id,
                             old.fingerprint.model_dump_json(), new_fp.model_dump_json(), "healing")

        if auto_approve:
            self.approve_heal(new.id, "auto")
            new = self.db.get_locator(new.id)
        logger.info(f"Healed '{old.element_description}': {old.id} v{old.version} -> {new.id} v{new.version} "
                    f"({'auto-approved' if auto_approve else 'pending review'})")
        return new

    def approve_heal(self, locator_id: str, reviewer: str = "reviewer") -> bool:
        new = self.db.get_locator(locator_id)
        if not new or new.review_status != 'pending':
            return False
        if new.replaces_id:
            self.db.set_locator_state(new.replaces_id, False, 'superseded')
        self.db.set_locator_state(new.id, True, 'approved')
        self.db.record_decision(new.id, 'approved', reviewer)
        return True

    def reject_heal(self, locator_id: str, reviewer: str = "reviewer") -> bool:
        new = self.db.get_locator(locator_id)
        if not new or new.review_status != 'pending':
            return False
        self.db.set_locator_state(new.id, False, 'rejected')
        self.db.record_decision(new.id, 'rejected', reviewer)
        return True

    def list_pending_heals(self, application: Optional[str] = None,
                           environment: Optional[str] = None) -> List[Locator]:
        return self.db.list_pending_heals(application, environment)

    def list_locators(self, application: str, environment: str, page: Optional[str] = None,
                      include_inactive: bool = False) -> List[Locator]:
        return self.db.list_locators(application, environment, page, include_inactive)

    def invalidate_locator(self, locator_id: str) -> bool:
        return self.db.invalidate_locator(locator_id) > 0

    def invalidate_page(self, application: str, environment: str, page: str) -> int:
        return self.db.invalidate_page(application, environment, page)

    def invalidate_all(self, application: str, environment: str) -> int:
        return self.db.invalidate_all(application, environment)

    def export_locators(self, application: str, environment: str) -> Dict[str, Any]:
        locators = self.db.list_locators(application, environment, include_inactive=True)
        return {
            "application": application,
            "environment": environment,
            "exported_at": datetime.now().isoformat(),
            "locators": [{
                "id": l.id, "page": l.page, "element_description": l.element_description,
                "primary_locator": l.primary_locator, "locator_strategy": l.locator_strategy,
                "fallback_locators": l.fallback_locators,
                "fingerprint": l.fingerprint.model_dump(),
                "version": l.version, "is_active": l.is_active,
                "review_status": l.review_status, "replaces_id": l.replaces_id,
                "created_at": l.created_at.isoformat(), "updated_at": l.updated_at.isoformat()
            } for l in locators]
        }

    def import_locators(self, import_data: Dict[str, Any]) -> int:
        count = 0
        for d in import_data.get("locators", []):
            try:
                self.db.save_locator(Locator(
                    id=d["id"], application=import_data["application"],
                    environment=import_data["environment"], page=d["page"],
                    element_description=d["element_description"],
                    primary_locator=d["primary_locator"], locator_strategy=d["locator_strategy"],
                    fallback_locators=d["fallback_locators"],
                    fingerprint=LocatorFingerprint(**d["fingerprint"]),
                    created_at=datetime.fromisoformat(d["created_at"]),
                    updated_at=datetime.fromisoformat(d["updated_at"]),
                    version=d["version"], is_active=d.get("is_active", True),
                    review_status=d.get("review_status", "approved"),
                    replaces_id=d.get("replaces_id")
                ))
                count += 1
            except Exception as e:
                logger.error(f"Error importing locator: {str(e)}")
        logger.info(f"Imported {count} locators")
        return count
