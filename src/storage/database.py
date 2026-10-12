import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from src.models import Locator, LocatorFingerprint, LLMUsage


class DatabaseManager:
    def __init__(self, db_path: str = "./data/framework.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def init_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS locator_reference (
                    id TEXT PRIMARY KEY,
                    application TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    page TEXT NOT NULL,
                    element_description TEXT NOT NULL,
                    primary_locator TEXT NOT NULL,
                    locator_strategy TEXT NOT NULL,
                    fallback_locators TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    version INTEGER NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    review_status TEXT NOT NULL DEFAULT 'approved',
                    replaces_id TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS locator_history (
                    id TEXT PRIMARY KEY,
                    locator_id TEXT NOT NULL,
                    old_locator_id TEXT,
                    old_fingerprint TEXT NOT NULL,
                    new_fingerprint TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    healed_at TIMESTAMP NOT NULL,
                    decision TEXT,
                    approved_by TEXT,
                    approved_at TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_plans (
                    application TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    test_case_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (application, environment, test_case_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    test_case_id TEXT NOT NULL,
                    step_no INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    timestamp TIMESTAMP NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_history (
                    id TEXT PRIMARY KEY,
                    suite_name TEXT NOT NULL,
                    application TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    total_duration_ms REAL NOT NULL,
                    total_test_cases INTEGER NOT NULL,
                    total_steps INTEGER NOT NULL,
                    passed_test_cases INTEGER NOT NULL,
                    failed_test_cases INTEGER NOT NULL,
                    blocked_test_cases INTEGER NOT NULL,
                    skipped_test_cases INTEGER NOT NULL,
                    passed_steps INTEGER NOT NULL,
                    failed_steps INTEGER NOT NULL,
                    blocked_steps INTEGER NOT NULL,
                    skipped_steps INTEGER NOT NULL,
                    total_tokens_used INTEGER NOT NULL,
                    total_llm_calls INTEGER NOT NULL,
                    cache_hit_count INTEGER NOT NULL,
                    fallback_hit_count INTEGER NOT NULL,
                    ai_identification_count INTEGER NOT NULL,
                    healed_count INTEGER NOT NULL,
                    cache_hit_rate REAL NOT NULL,
                    false_heal_rate REAL NOT NULL,
                    total_cost_usd REAL NOT NULL,
                    cost_per_100_steps REAL NOT NULL
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_locator_key ON locator_reference(application, environment, page, element_description)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_run ON llm_usage(run_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_history_app ON run_history(application, environment)")

    # ---------- locator references ----------

    @staticmethod
    def _row_to_locator(row) -> Locator:
        return Locator(
            id=row['id'],
            application=row['application'],
            environment=row['environment'],
            page=row['page'],
            element_description=row['element_description'],
            primary_locator=row['primary_locator'],
            locator_strategy=row['locator_strategy'],
            fallback_locators=json.loads(row['fallback_locators']),
            fingerprint=LocatorFingerprint(**json.loads(row['fingerprint'])),
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            version=row['version'],
            is_active=bool(row['is_active']),
            review_status=row['review_status'],
            replaces_id=row['replaces_id']
        )

    def save_locator(self, locator: Locator) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO locator_reference
                (id, application, environment, page, element_description, primary_locator,
                 locator_strategy, fallback_locators, fingerprint, created_at, updated_at,
                 version, is_active, review_status, replaces_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                locator.id, locator.application, locator.environment, locator.page,
                locator.element_description, locator.primary_locator, locator.locator_strategy,
                json.dumps(locator.fallback_locators), locator.fingerprint.model_dump_json(),
                locator.created_at.isoformat(), locator.updated_at.isoformat(),
                locator.version, 1 if locator.is_active else 0,
                locator.review_status, locator.replaces_id
            ))

    def get_locator(self, locator_id: str) -> Optional[Locator]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM locator_reference WHERE id = ?", (locator_id,)).fetchone()
            return self._row_to_locator(row) if row else None

    def get_reference(self, application: str, environment: str, page: str,
                      element_description: str, review_status: str) -> Optional[Locator]:
        with self.get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM locator_reference
                WHERE application = ? AND environment = ? AND page = ? AND element_description = ?
                  AND is_active = 1 AND review_status = ?
                ORDER BY version DESC LIMIT 1
            """, (application, environment, page, element_description, review_status)).fetchone()
            return self._row_to_locator(row) if row else None

    def list_locators(self, application: str, environment: str, page: Optional[str] = None,
                      include_inactive: bool = False) -> List[Locator]:
        query = "SELECT * FROM locator_reference WHERE application = ? AND environment = ?"
        params: List[Any] = [application, environment]
        if page:
            query += " AND page = ?"
            params.append(page)
        if not include_inactive:
            query += " AND is_active = 1"
        query += " ORDER BY page, element_description, version"
        with self.get_connection() as conn:
            return [self._row_to_locator(r) for r in conn.execute(query, params).fetchall()]

    def list_pending_heals(self, application: Optional[str] = None,
                           environment: Optional[str] = None) -> List[Locator]:
        query = "SELECT * FROM locator_reference WHERE review_status = 'pending' AND is_active = 1"
        params: List[Any] = []
        if application:
            query += " AND application = ?"
            params.append(application)
        if environment:
            query += " AND environment = ?"
            params.append(environment)
        with self.get_connection() as conn:
            return [self._row_to_locator(r) for r in conn.execute(query, params).fetchall()]

    def list_known_targets(self, application: str, environment: str, limit: int = 150) -> List[str]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT element_description FROM locator_reference
                WHERE application = ? AND environment = ? AND is_active = 1
                ORDER BY created_at LIMIT ?
            """, (application, environment, limit)).fetchall()
            return [r['element_description'] for r in rows]

    def set_locator_state(self, locator_id: str, is_active: bool, review_status: str) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE locator_reference SET is_active = ?, review_status = ?, updated_at = ? WHERE id = ?
            """, (1 if is_active else 0, review_status, datetime.now().isoformat(), locator_id))

    def invalidate_locator(self, locator_id: str) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("UPDATE locator_reference SET is_active = 0 WHERE id = ?", (locator_id,))
            return cur.rowcount

    def invalidate_page(self, application: str, environment: str, page: str) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("""
                UPDATE locator_reference SET is_active = 0
                WHERE application = ? AND environment = ? AND page = ? AND is_active = 1
            """, (application, environment, page))
            return cur.rowcount

    def invalidate_all(self, application: str, environment: str) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("""
                UPDATE locator_reference SET is_active = 0
                WHERE application = ? AND environment = ? AND is_active = 1
            """, (application, environment))
            return cur.rowcount

    # ---------- heal history ----------

    def save_history(self, history_id: str, locator_id: str, old_locator_id: Optional[str],
                     old_fingerprint: str, new_fingerprint: str, reason: str) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO locator_history
                (id, locator_id, old_locator_id, old_fingerprint, new_fingerprint, reason, healed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (history_id, locator_id, old_locator_id, old_fingerprint, new_fingerprint,
                  reason, datetime.now().isoformat()))

    def record_decision(self, new_locator_id: str, decision: str, approved_by: str) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE locator_history SET decision = ?, approved_by = ?, approved_at = ?
                WHERE locator_id = ?
            """, (decision, approved_by, datetime.now().isoformat(), new_locator_id))

    def get_history(self, locator_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM locator_history WHERE locator_id = ? ORDER BY healed_at",
                                (locator_id,)).fetchall()
            return [dict(r) for r in rows]

    # ---------- persisted test plans ----------

    def get_plan(self, application: str, environment: str, test_case_id: str,
                 content_hash: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("""
                SELECT plan_json FROM test_plans
                WHERE application = ? AND environment = ? AND test_case_id = ? AND content_hash = ?
            """, (application, environment, test_case_id, content_hash)).fetchone()
            return json.loads(row['plan_json']) if row else None

    def save_plan(self, application: str, environment: str, test_case_id: str,
                  content_hash: str, plan: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO test_plans
                (application, environment, test_case_id, content_hash, plan_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (application, environment, test_case_id, content_hash,
                  json.dumps(plan), datetime.now().isoformat()))

    def delete_plans(self, application: str, environment: str,
                     test_case_ids: Optional[List[str]] = None) -> int:
        query = "DELETE FROM test_plans WHERE application = ? AND environment = ?"
        params: List[Any] = [application, environment]
        if test_case_ids:
            query += " AND test_case_id IN (%s)" % ",".join("?" * len(test_case_ids))
            params.extend(test_case_ids)
        with self.get_connection() as conn:
            return conn.execute(query, params).rowcount

    # ---------- LLM usage and run history ----------

    def log_llm_usage(self, usage: LLMUsage) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO llm_usage
                (id, run_id, test_case_id, step_no, user_id, agent_name, reason, model,
                 input_tokens, output_tokens, latency_ms, cost_usd, status, error_message, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                usage.id, usage.run_id, usage.test_case_id, usage.step_no, usage.user_id,
                usage.agent_name, usage.reason, usage.model, usage.input_tokens,
                usage.output_tokens, usage.latency_ms, usage.cost_usd, usage.status,
                usage.error_message, usage.timestamp.isoformat()
            ))

    def get_llm_usage_for_run(self, run_id: str) -> List[LLMUsage]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM llm_usage WHERE run_id = ? ORDER BY timestamp",
                                (run_id,)).fetchall()
            return [LLMUsage(
                id=r['id'], run_id=r['run_id'], test_case_id=r['test_case_id'],
                step_no=r['step_no'], user_id=r['user_id'], agent_name=r['agent_name'],
                reason=r['reason'], model=r['model'], input_tokens=r['input_tokens'],
                output_tokens=r['output_tokens'], latency_ms=r['latency_ms'],
                cost_usd=r['cost_usd'], status=r['status'], error_message=r['error_message'],
                timestamp=datetime.fromisoformat(r['timestamp'])
            ) for r in rows]

    def save_run_history(self, run_data: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO run_history
                (id, suite_name, application, environment, start_time, end_time,
                 total_duration_ms, total_test_cases, total_steps, passed_test_cases,
                 failed_test_cases, blocked_test_cases, skipped_test_cases, passed_steps,
                 failed_steps, blocked_steps, skipped_steps, total_tokens_used,
                 total_llm_calls, cache_hit_count, fallback_hit_count,
                 ai_identification_count, healed_count, cache_hit_rate,
                 false_heal_rate, total_cost_usd, cost_per_100_steps)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuple(run_data.values()))

    def get_run_history(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM run_history WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def get_all_runs_for_application(self, application: str,
                                    environment: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM run_history WHERE application = ? AND environment = ?
                ORDER BY start_time DESC LIMIT ?
            """, (application, environment, limit)).fetchall()
            return [dict(r) for r in rows]
