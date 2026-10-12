import re
import json
import uuid
import time
import hashlib
import asyncio
import yaml
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from src.models import (
    TestCase, TestStep, ExecutionResult, StepResult, RunSummary
)
from src.storage import DatabaseManager
from src.tools import (
    ReadExcelTool, WriteExcelTool, BrowserTool, LocatorStoreTool,
    ScreenshotTool, ReportTool
)
from src.utils import get_logger, log_run_history, URLValidator
from .locator_agent import LocatorAgent
from .verification_agent import VerificationAgent, _norm


logger = get_logger(__name__, log_file="./logs/framework.log")

PLANNER_VERSION = "4"
ACTIONS = {'navigate', 'click', 'fill', 'select', 'hover', 'scroll', 'upload', 'press', 'wait', 'observe'}
ACTION_ALIASES = {'type': 'fill', 'enter': 'fill', 'input': 'fill', 'verify': 'observe', 'assert': 'observe',
                  'check': 'click', 'choose': 'click', 'open': 'navigate', 'goto': 'navigate', 'submit': 'click'}
SENSITIVE_KEY = re.compile(r'pass|pwd|card|cvc|cvv|expiry|email|mobile|phone|zip|address|dob|^name$|username', re.I)


class AccountStore:
    """Test accounts created by the suite, persisted so later tests and later runs can log in with them."""

    def __init__(self, path: str = "data/test_credentials.json"):
        self.path = Path(path)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data.get('accounts', []) if isinstance(data, dict) else []
        except Exception:
            return []

    def _save(self, accounts: List[Dict[str, Any]]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"accounts": accounts}, indent=2), encoding='utf-8')

    def current(self, application: str) -> Optional[Dict[str, Any]]:
        live = [a for a in self._load() if a['application'] == application and not a.get('deleted')]
        return live[-1] if live else None

    def add(self, application: str, email: str, password: Optional[str], name: Optional[str]):
        accounts = [a for a in self._load() if a['email'] != email]
        accounts.append({"application": application, "email": email, "password": password or "",
                         "name": name or "", "created_at": datetime.now().isoformat(timespec='seconds'),
                         "deleted": False})
        self._save(accounts)

    def mark_deleted(self, application: str, email: str):
        accounts = self._load()
        for a in accounts:
            if a['application'] == application and a['email'] == email:
                a['deleted'] = True
        self._save(accounts)


class TestContext:
    """Resolved test data and runtime values for one test case."""

    def __init__(self, base_url: str, account: Optional[Dict[str, Any]]):
        self.base_url = base_url
        self.account = account
        self.timestamp = str(int(time.time()))
        self.data: Dict[str, str] = {}
        self.keys: Dict[str, str] = {}
        self.generated_email: Optional[str] = None
        self.extra: List[str] = []
        self.created_seen = False

    def lookup(self, key: str) -> Optional[str]:
        k = _norm(key)
        if k == 'base_url':
            return self.base_url
        if k == 'timestamp':
            return self.timestamp
        if k.startswith('account.'):
            field = k.split('.', 1)[1]
            field = 'email' if field in ('username', 'user', 'login') else field
            return (self.account or {}).get(field)
        if k in self.data:
            return self.data[k]
        if '.' in k:
            root, part = k.split('.', 1)
            return self._derived(self.data.get(root), part)
        return None

    @staticmethod
    def _derived(value: Optional[str], part: str) -> Optional[str]:
        if not value:
            return None
        if part in ('first', 'last'):
            words = value.split()
            return words[0] if part == 'first' else ' '.join(words[1:]) or words[0]
        m = re.fullmatch(r'\s*(\d{1,2})[-/ ]([A-Za-z]{3,9})[-/ ](\d{4})\s*', value)
        if m and part in ('day', 'month', 'year'):
            return {'day': m.group(1), 'month': m.group(2), 'year': m.group(3)}[part]
        m = re.fullmatch(r'\s*(\d{1,2})\s*/\s*(\d{4})\s*', value)
        if m and part in ('month', 'year'):
            return {'month': m.group(1), 'year': m.group(2)}[part]
        return None

    def fill(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return re.sub(r'\{\{\s*([^{}]+?)\s*\}\}',
                      lambda m: self.lookup(m.group(1)) if self.lookup(m.group(1)) is not None else m.group(0), text)


class ExecutionWorkflow:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"
        self.stats: Dict[str, Any] = {
            'lookups': 0, 'cache_hits': 0, 'fallback_hits': 0, 'ai_identifications': 0,
            'heals': 0, 'llm_calls': 0, 'total_tokens': 0, 'total_cost': 0.0
        }

        self.db_manager = DatabaseManager(config.get('database', {}).get('path', './data/framework.db'))
        self.locator_store = LocatorStoreTool(self.db_manager)
        self.screenshot_tool = ScreenshotTool()
        self.report_tool = ReportTool()
        fw = config.get('framework', {})
        self.browser_tool = BrowserTool(headless=fw.get('headless_mode', True), slow_mo=int(fw.get('slow_mo', 0)))
        self.hold_seconds = float(fw.get('hold_seconds', 0))
        self.locator_agent = LocatorAgent(config, self.db_manager, self.browser_tool, self.stats)
        self.locator_agent.run_id = self.run_id
        self.verification_agent = VerificationAgent(config, self.browser_tool)
        self.accounts = AccountStore()

        with open(config.get('applications_config', './config/applications.yaml'), 'r') as f:
            self.apps_config = yaml.safe_load(f)
        self.url_validator = URLValidator(self.apps_config.get('allowed_domains', []))
        self.execution_results: List[ExecutionResult] = []

    # ------------------------------------------------------------------ suite

    async def execute_test_suite(self, excel_file: str, application: str, environment: str,
                                 suite_name: str, only: Optional[List[str]] = None,
                                 replan: bool = False) -> RunSummary:
        start_time = datetime.now()
        logger.info(f"Starting test execution: {suite_name} ({self.run_id})")

        app_cfg = self.apps_config.get('applications', {}).get(application)
        if not app_cfg or environment not in app_cfg.get('environments', {}):
            raise ValueError(f"Unknown application/environment: {application}/{environment}")
        base_url = self._base_url(application, environment)
        if not self.url_validator.validate_url(base_url):
            raise ValueError(f"Refusing to run: {base_url} is not on the allow-list of approved test environments")

        test_cases = ReadExcelTool(excel_file).read_test_suite(application, environment)
        logger.info(f"Read {len(test_cases)} test cases from {excel_file}")

        selected = [tc for tc in test_cases if tc.execute and (not only or tc.test_case_id in only)]
        selected_ids = {tc.test_case_id for tc in selected}
        if replan:
            removed = self.db_manager.delete_plans(application, environment, sorted(selected_ids))
            logger.info(f"Discarded {removed} stored test plans; they will be regenerated (first-learning tokens)")

        rps = app_cfg.get('rate_limit', {}).get('requests_per_second', 1) or 1
        self.browser_tool.configure(app_cfg.get('block_url_patterns'), 1.0 / rps)
        await self.browser_tool.initialize()

        try:
            for tc in test_cases:
                if not tc.execute:
                    self.execution_results.append(self._skipped(tc, application, environment))
                elif tc.test_case_id in selected_ids:
                    result = await self._run_test_case(tc, application, environment, test_cases)
                    self.execution_results.append(result)
        finally:
            await self.browser_tool.close()

        end_time = datetime.now()
        run_summary = self._generate_run_summary(suite_name, application, environment, start_time, end_time)

        written = WriteExcelTool(excel_file).write_all(self.execution_results, run_summary)
        logger.info(f"Results written to {written}")
        pending_heals = self.locator_store.list_pending_heals(application, environment)
        heal_details = []
        for h in pending_heals:
            old = self.db_manager.get_locator(h.replaces_id) if h.replaces_id else None
            describe = lambda l: f"<{l.fingerprint.tag}> text={l.fingerprint.text!r} via {l.locator_strategy}" if l else "-"
            heal_details.append({'id': h.id, 'page': h.page, 'element_description': h.element_description,
                                 'version': h.version, 'review_status': h.review_status,
                                 'old': describe(old), 'new': describe(h)})
        self.report_tool.generate_json_report(run_summary, self.execution_results, heal_details)
        self.report_tool.generate_text_report(run_summary, self.execution_results, heal_details)
        self.report_tool.generate_markdown_report(run_summary, self.execution_results, heal_details)
        self._log_run_history(run_summary)

        if pending_heals:
            logger.warning(f"{len(pending_heals)} healed reference(s) await review: run with --list-heals, "
                           f"then --approve-heal/--reject-heal")
        logger.info(f"Test execution completed: {self.run_id}")
        return run_summary

    def _skipped(self, tc: TestCase, application: str, environment: str) -> ExecutionResult:
        now = datetime.now()
        return ExecutionResult(run_id=self.run_id, test_case_id=tc.test_case_id, test_case_title=tc.test_case_title,
                               application=application, environment=environment, status="Skipped",
                               actual_result="Execute is not Y", steps=[], total_execution_time_ms=0.0,
                               row_index=tc.row_index, started_at=now, completed_at=now)

    # ------------------------------------------------------------------ planning

    async def _get_plan(self, tc: TestCase, application: str, environment: str,
                        app_cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int, str]:
        login = app_cfg.get('login', {}).get('description', '')
        content = json.dumps({'v': PLANNER_VERSION, 'title': tc.test_case_title, 'steps': tc.source_steps,
                              'data': tc.test_data, 'expected': tc.expected_result, 'login': login},
                             sort_keys=True)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        stored = self.db_manager.get_plan(application, environment, tc.test_case_id, content_hash)
        if stored:
            logger.info(f"{tc.test_case_id}: stored plan reused ({len(stored['steps'])} steps, 0 tokens)")
            return stored, 0, ""

        if not self.locator_agent.client:
            return None, 0, "no stored plan and no LLM available"
        if self.locator_agent.llm_error:
            return None, 0, f"no stored plan and the LLM is unavailable: {self.locator_agent.llm_error}"

        raw_data = self._parse_raw_data(tc.test_data)
        data_lines, placeholders = [], []
        for key, value in raw_data.items():
            hidden = bool(SENSITIVE_KEY.search(key)) or '@' in value or bool(re.search(r'\d{6,}', value))
            data_lines.append(f"  {key} = {'(value hidden)' if hidden else value}   -> {{{{{key}}}}}")
            placeholders.append(f"{{{{{key}}}}}")
            for part in ('first', 'last', 'day', 'month', 'year'):
                if part in ('first', 'last') and not re.search(r'name', key, re.I):
                    continue
                if TestContext._derived(self._clean_value(value), part):
                    placeholders.append(f"{{{{{key}.{part}}}}}")
        placeholders += ["{{base_url}}", "{{timestamp}}", "{{account.email}}", "{{account.password}}", "{{account.name}}"]

        known = self.db_manager.list_known_targets(application, environment)
        known_block = ("ELEMENT NAMES ALREADY LEARNED FOR THIS APPLICATION (when you mean one of these elements, copy "
                       "its wording exactly so it is reused instead of identified again):\n"
                       + "\n".join(f"  - {k}" for k in known) + "\n\n") if known else ""

        user = (
            f"APPLICATION: {app_cfg.get('name')} ({app_cfg.get('base_url')})\n"
            + (f"LOGIN METHOD: {login}\n" if login else "")
            + f"TEST CASE: {tc.test_case_id} - {tc.test_case_title}\n\nMANUAL STEPS:\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(tc.source_steps, 1))
            + "\n\nTEST DATA (refer to every value only through its placeholder):\n"
            + ("\n".join(data_lines) if data_lines else "  (none)")
            + f"\n\nAVAILABLE PLACEHOLDERS: {', '.join(placeholders)}\n\n"
            + f"EXPECTED RESULT OF THE WHOLE TEST:\n{tc.expected_result}\n\n"
            + known_block
            + (f"APPLICATION NOTES FROM THE OWNER (authoritative: they describe the real pages, so plan "
               f"accordingly and split values across fields exactly as described):\n{app_cfg['planner_hints']}\n\n"
               if app_cfg.get('planner_hints') else "")
            + PLANNER_RULES
        )
        system = ("You convert manual test steps into atomic browser actions for an automated executor. "
                  "You never write CSS/XPath selectors and never invent test data.")
        result, tokens = self.locator_agent.llm_json(system, user, tc.test_case_id, 0, "TestPlanner",
                                                     "first_learning", max_tokens=2500)
        plan = self._validate_plan(result)
        if plan:
            plan = self._drop_invented_data(plan, tc)
        if not plan:
            if self.locator_agent.llm_error:
                return None, tokens, f"the LLM is unavailable: {self.locator_agent.llm_error}"
            return None, tokens, "planner returned no usable steps"
        self.db_manager.save_plan(application, environment, tc.test_case_id, content_hash, plan)
        logger.info(f"{tc.test_case_id}: planned {len(plan['steps'])} actions ({tokens} tokens), plan stored")
        return plan, tokens, ""

    @staticmethod
    def _drop_invented_data(plan: Dict[str, Any], tc: TestCase) -> Optional[Dict[str, Any]]:
        """A fill/select value must be a placeholder or text that exists in the workbook; anything else is invented."""
        known = _norm(' '.join(tc.source_steps) + ' ' + (tc.test_data or '') + ' ' + tc.expected_result)
        kept, new_index, count = [], {}, 0
        for i, s in enumerate(plan['steps'], 1):
            value = s.get('value')
            if s['action'] in ('fill', 'select') and value and '{{' not in value and _norm(value) not in known:
                logger.warning(f"{tc.test_case_id}: dropped planned step with a value that is not in the workbook "
                               f"({s['action']} {s.get('target')})")
            else:
                kept.append(s)
                count += 1
            new_index[i] = count
        if not kept:
            return None

        # Two fields that share a label (expiry month / expiry year) must not share a target description, or both
        # steps would resolve to the same element. Consecutive fills of one target are told apart by value part.
        for a, b in zip(kept, kept[1:]):
            if a['action'] in ('fill', 'select') and b['action'] in ('fill', 'select') and a['target'] \
                    and a['target'] == b['target'] and a.get('value') != b.get('value'):
                for s in (a, b):
                    part = re.search(r'\{\{[^{}]*\.(\w+)\s*\}\}', s.get('value') or '')
                    if part:
                        s['target'] = f"{s['target']} ({part.group(1)} field)"
        checks = []
        for c in plan.get('checks', []):
            try:
                after = int(c.get('after_step') or len(plan['steps']))
            except (TypeError, ValueError):
                after = len(plan['steps'])
            c = dict(c)
            c['after_step'] = max(1, new_index.get(min(max(after, 1), len(plan['steps'])), len(kept)))
            checks.append(c)
        return {'steps': kept, 'checks': checks}

    @staticmethod
    def _validate_plan(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not result or not isinstance(result.get('steps'), list):
            return None
        steps = []
        for s in result['steps']:
            if not isinstance(s, dict):
                continue
            action = str(s.get('action', '')).strip().lower()
            action = ACTION_ALIASES.get(action, action)
            if action not in ACTIONS:
                continue
            steps.append({'action': action,
                          'target': (str(s['target']).strip() if s.get('target') else None),
                          'value': (str(s['value']) if s.get('value') is not None else None),
                          'source_step': s.get('source_step')})
        if not steps:
            return None
        checks = [c for c in (result.get('checks') or []) if isinstance(c, dict) and c.get('text')]
        return {'steps': steps, 'checks': checks}

    # ------------------------------------------------------------------ test data

    @staticmethod
    def _clean_value(value: str) -> str:
        value = value.strip()
        if re.fullmatch(r'\([^()]*\)', value):
            return value
        # only annotations that carry a number or currency, e.g. "(Rs. 500)" or "(<1 MB)", are not part of the value;
        # word-only brackets such as "Price (low to high)" are
        return re.sub(r'\s*\((?=[^()]*[\d$€£₹])[^()]*\)\s*$', '', value).strip()

    @classmethod
    def _parse_raw_data(cls, raw: Optional[str]) -> Dict[str, str]:
        data: Dict[str, str] = {}
        if not raw:
            return data
        text = raw.replace('\r', '')
        lines = text.split('\n') if '\n' in text else re.split(r',\s*(?=[A-Za-z][\w ]*:)', text)
        for line in lines:
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            if key.strip():
                data[key.strip()] = value.strip()
        return data

    def _build_context(self, tc: TestCase, base_url: str, account: Optional[Dict[str, Any]]) -> TestContext:
        ctx = TestContext(base_url, account)
        for key, value in self._parse_raw_data(tc.test_data).items():
            value = self._clean_value(value)
            if value.lower() in ('(blank)', '(empty)', ''):
                value = ''
            elif re.fullmatch(r'<[^>]*(email|user\s*name)[^>]*>', value, re.I):
                value = (account or {}).get('email', value)
            else:
                if '<timestamp>' in value:
                    value = value.replace('<timestamp>', ctx.timestamp)
                    if '@' in value:
                        ctx.generated_email = value
            ctx.data[_norm(key)] = value
            ctx.keys[_norm(key)] = key
        return ctx

    # ------------------------------------------------------------------ accounts

    async def _ensure_account(self, application: str, environment: str, app_cfg: Dict[str, Any],
                              all_cases: List[TestCase]) -> Optional[Dict[str, Any]]:
        fixed = app_cfg.get('test_account')
        if fixed:
            return dict(fixed)
        account = self.accounts.current(application)
        if account:
            return account
        reg_id = app_cfg.get('registration_test_case')
        reg = next((t for t in all_cases if t.test_case_id == reg_id), None)
        if not reg:
            return None
        logger.info(f"No usable test account: running {reg_id} to register one")
        await self._run_test_case(reg, application, environment, all_cases, provisioning=True)
        return self.accounts.current(application)

    # ------------------------------------------------------------------ test case execution

    async def _run_test_case(self, tc: TestCase, application: str, environment: str,
                             all_cases: List[TestCase], provisioning: bool = False) -> ExecutionResult:
        started = datetime.now()
        app_cfg = self.apps_config['applications'][application]
        base_url = self._base_url(application, environment)
        step_results: List[StepResult] = []
        tokens_total = 0
        screenshot = None
        status, summary = "Blocked", ""

        try:
            plan, plan_tokens, error = await self._get_plan(tc, application, environment, app_cfg)
            tokens_total += plan_tokens
            if not plan:
                return self._finish(tc, application, environment, started, "Blocked",
                                    f"Could not plan test case: {error}", step_results, tokens_total, None)

            plan_text = json.dumps(plan)
            needs_account = '{{account.' in plan_text or bool(re.search(r'<[^>]*(email|user)[^>]*>', tc.test_data or '', re.I))
            account = None
            if needs_account and not provisioning:
                account = await self._ensure_account(application, environment, app_cfg, all_cases)
                if not account:
                    return self._finish(tc, application, environment, started, "Blocked",
                                        "This test needs a registered account but none is available and none "
                                        "could be created", step_results, tokens_total, None)
            ctx = self._build_context(tc, base_url, account)

            steps = [TestStep(step_no=i, action=s['action'], target=s['target'], value=s['value'],
                              source_step=s['source_step']) for i, s in enumerate(plan['steps'], 1)]
            checks = self._build_checks(tc, plan, len(steps), ctx)
            if not checks:
                return self._finish(tc, application, environment, started, "Blocked",
                                    "Expected result has no machine-checkable text; refusing to report a pass "
                                    "without verification", step_results, tokens_total, None)

            await self.browser_tool.new_context()
            if not await self.browser_tool.navigate_to(base_url):
                return self._finish(tc, application, environment, started, "Blocked",
                                    f"Could not open {base_url}", step_results, tokens_total, None)

            blocked_at = None
            for step in steps:
                if blocked_at:
                    step_results.append(StepResult(step_no=step.step_no, status="Blocked", action=step.action,
                                                   target=step.target,
                                                   actual_result=f"Not executed: step {blocked_at} was blocked"))
                    continue
                result = await self._execute_step(step, ctx, tc, application, environment, app_cfg)
                if result.status == "Pass":
                    await self._evaluate_checks(result, step.step_no, len(steps), checks, ctx)
                tokens_total += result.tokens_used
                if result.status in ("Fail", "Blocked") and self.config.get('framework', {}).get('screenshot_on_failure', True):
                    path = self.screenshot_tool.get_screenshot_path(self.run_id, tc.test_case_id, step.step_no, application)
                    if await self.browser_tool.take_screenshot(path):
                        result.screenshot_path = path
                        screenshot = screenshot or path
                step_results.append(result)
                if result.status == "Blocked":
                    blocked_at = step.step_no

            if any(r.status == "Blocked" for r in step_results):
                status = "Blocked"
                first = next(r for r in step_results if r.status == "Blocked" and not (r.actual_result or '').startswith('Not executed'))
                summary = f"Step {first.step_no} blocked: {first.actual_result}"
            elif any(r.status == "Fail" for r in step_results):
                status = "Fail"
                summary = "; ".join(f"Step {r.step_no}: {r.actual_result}" for r in step_results if r.status == "Fail")
            else:
                status = "Pass"
                summary = f"All {len(steps)} steps executed. Verified on screen: " + \
                          "; ".join(f"'{c['text']}'" for c in checks if c['done'])
                soft = [c['text'] for c in checks if not c['done']]
                if soft:
                    summary += ". Not observed (advisory only): " + "; ".join(f"'{t}'" for t in soft)
                if ctx.generated_email and not app_cfg.get('account_markers', {}).get('created'):
                    self.accounts.add(application, ctx.generated_email, ctx.data.get('password'), ctx.data.get('name'))
        except Exception as e:
            logger.error(f"{tc.test_case_id}: unexpected error: {str(e)[:200]}", exc_info=True)
            status, summary = "Blocked", f"Unexpected error: {str(e)[:200]}"
        finally:
            if self.hold_seconds and not provisioning and self.browser_tool.page:
                await asyncio.sleep(self.hold_seconds)  # demo aid: leave the final page visible
            await self.browser_tool.close_context()

        if provisioning:
            logger.info(f"Provisioning run of {tc.test_case_id} finished: {status}")
        return self._finish(tc, application, environment, started, status, summary, step_results,
                            tokens_total, screenshot)

    def _build_checks(self, tc: TestCase, plan: Dict[str, Any], n_steps: int,
                      ctx: TestContext) -> List[Dict[str, Any]]:
        """Checks come only from the workbook text: quoted messages and amounts in the expected result, plus any
        planner-selected fragment that appears verbatim in the expected result or test data.

        The planner's after_step is only a hint: a check is satisfied if its text is observed at any step from one
        before the hint to the end of the test, so a miscounted hint cannot cause a false Fail, and an expected
        message that never appears is always a Fail. Quoted messages the planner left out may appear at any step."""
        mandatory = {_norm(p) for p in self.verification_agent.mandatory_phrases(tc.expected_result)}
        bracketed = {_norm(x) for chunk in re.findall(r'\(([^()]*)\)', tc.expected_result) for x in chunk.split(',')}
        data_values = [_norm(self._clean_value(v)) for v in self._parse_raw_data(tc.test_data).values()]

        def binding(text: str) -> bool:
            """Literal text from the workbook (quoted message, amount, bracketed item, test-data value) is binding;
            a fragment of free prose such as 'quantity 1' is only advisory because it is not an on-screen string."""
            n = _norm(text)
            return n in mandatory or n in bracketed or any(len(v) >= 3 and (n in v or v in n) for v in data_values)

        checks: List[Dict[str, Any]] = []
        seen = set()
        for c in plan.get('checks', []):
            try:
                after = min(max(int(c.get('after_step') or n_steps), 1), n_steps)
            except (TypeError, ValueError):
                after = n_steps
            # brackets in the workbook sentence are formatting, e.g. "Price (Rs. 500)": the bracketed values are
            # what must appear on screen, not the descriptive word before them
            raw = str(c.get('text', '')).strip().strip('\'"')
            inner = [x for chunk in re.findall(r'\(([^()]*)\)', raw) for x in chunk.split(',')]
            for text in (p.strip().strip('\'"') for p in (inner or [raw])):
                if len(text) < 2 or _norm(text) in seen:
                    continue
                if not VerificationAgent.grounded(text, tc.expected_result, tc.test_data or ''):
                    logger.info(f"{tc.test_case_id}: dropped check not found in the workbook text: '{text[:60]}'")
                    continue
                seen.add(_norm(text))
                # end-state checks must hold on the final page, not merely appear on some earlier page
                start = n_steps if after == n_steps else max(1, after - 1)
                checks.append({'text': text, 'after_step': after, 'start': start, 'done': False,
                               'binding': binding(text)})
        for phrase in self.verification_agent.mandatory_phrases(tc.expected_result):
            if _norm(phrase) not in seen:
                seen.add(_norm(phrase))
                checks.append({'text': phrase, 'after_step': n_steps,
                               'start': n_steps if self.verification_agent.is_amount(phrase) else 1,
                               'done': False, 'binding': True})

        for c in checks:
            c['text'] = self._resolve_check_text(c['text'], ctx)
        return checks

    async def _evaluate_checks(self, result: StepResult, step_no: int, last_step: int,
                               checks: List[Dict[str, Any]], ctx: TestContext):
        active = [c for c in checks if not c['done'] and c['start'] <= step_no]
        if not active:
            return
        extras = ctx.extra + self.browser_tool.dialog_messages
        is_last = step_no == last_step
        must = [c for c in active if c['binding']]
        advisory = [c for c in active if not c['binding']]
        timeout = 6.0 if is_last else (3.0 if any(c['after_step'] == step_no for c in must) else 0.0)
        found: List[str] = []
        if must:
            found, _ = await self.verification_agent.wait_for([c['text'] for c in must], extras, timeout)
        if advisory:
            corpus = await self.verification_agent.observe(extras)
            found += [c['text'] for c in advisory if VerificationAgent.present(c['text'], corpus)]
        for c in active:
            if c['text'] in found:
                c['done'] = True
        if found:
            result.actual_result = f"{result.actual_result}. Verified: " + "; ".join(f"'{f}'" for f in found)
        if is_last:
            missing = [c['text'] for c in checks if c['binding'] and not c['done']]
            soft = [c['text'] for c in checks if not c['binding'] and not c['done']]
            if missing:
                result.status = "Fail"
                result.actual_result = "Expected text not displayed: " + "; ".join(f"'{t}'" for t in missing)
            elif soft:
                result.actual_result += ". Not observed (advisory, free-text fragment): " + "; ".join(f"'{t}'" for t in soft)

    @staticmethod
    def _resolve_check_text(text: str, ctx: TestContext) -> str:
        name = (ctx.account or {}).get('name') or ctx.data.get('name')
        if name:
            text = re.sub(r'<\s*(user\s*name|username|name)\s*>', name, text, flags=re.I)
        return text

    async def _execute_step(self, step: TestStep, ctx: TestContext, tc: TestCase, application: str,
                            environment: str, app_cfg: Dict[str, Any]) -> StepResult:
        t0 = time.perf_counter()
        target = ctx.fill(step.target)
        value = ctx.fill(step.value)
        res = StepResult(step_no=step.step_no, status="Pass", action=step.action, target=target)

        def done(status: str, actual: str) -> StepResult:
            res.status, res.actual_result = status, actual
            res.execution_time_ms = (time.perf_counter() - t0) * 1000
            return res

        if (target and '{{' in target) or (value and '{{' in value):
            return done("Blocked", "Test data placeholder could not be resolved from the workbook")

        action = step.action
        try:
            if action == 'navigate':
                url = value or ctx.base_url
                if not url.startswith('http'):
                    url = ctx.base_url.rstrip('/') + '/' + url.lstrip('/')
                if not self.url_validator.validate_url(url):
                    return done("Blocked", f"Refused: {url} is not on the allow-list of approved test environments")
                if not await self.browser_tool.navigate_to(url):
                    return done("Blocked", f"Could not open {url}")
                res.actual_result = f"Opened {url}"
            elif action == 'wait':
                await asyncio.sleep(min(float(re.sub(r'[^\d.]', '', value or '1') or 1), 10))
                res.actual_result = "Waited"
            elif action == 'press':
                if not await self.browser_tool.press(value or 'Enter'):
                    return done("Blocked", f"Could not press {value}")
                await self.browser_tool.settle()
                res.actual_result = f"Pressed {value}"
            elif action == 'observe':
                res.actual_result = "Observed the page"
            elif action == 'scroll' and (not target or re.fullmatch(
                    r'(?i)\s*(the\s+)?(page\s+)?(footer|bottom|end|top|header)(\s+of\s+the\s+page)?\s*', target)):
                where = 'top' if re.search(r'top|header', f"{target or ''} {value or ''}", re.I) else 'bottom'
                await self.browser_tool.scroll(None, where)
                res.actual_result = f"Scrolled to the {where} of the page"
            else:
                if not target:
                    return done("Blocked", f"'{action}' step has no target element")
                resolution = await self.locator_agent.resolve_element(
                    application, environment, tc.test_case_id, step.step_no, target, action,
                    must_contain=self._literals_in(target, ctx) if action in ('click', 'hover') else None)
                res.element_source, res.tokens_used = resolution.source, resolution.tokens
                if not resolution.handle:
                    return done("Blocked", resolution.reason or f"Element not identified: {target}")
                handle = resolution.handle

                if action == 'click':
                    refusal = await self._payment_refusal(handle, ctx, app_cfg)
                    if refusal:
                        return done("Blocked", refusal)
                    if not await self.browser_tool.click(handle):
                        return done("Blocked", f"Click failed on: {target}")
                    ctx.extra = await self.browser_tool.validation_messages(handle)
                    await self.browser_tool.settle()
                    res.actual_result = f"Clicked: {target}"
                elif action == 'fill':
                    if value in (None, ''):
                        res.actual_result = f"Left blank as specified: {target}"
                    elif not await self.browser_tool.fill(handle, value):
                        return done("Blocked", f"Could not fill: {target}")
                    else:
                        res.actual_result = f"Filled: {target}"
                elif action == 'select':
                    if not await self.browser_tool.select(handle, value or ''):
                        return done("Blocked", f"Could not select an option in: {target}")
                    res.actual_result = f"Selected option in: {target}"
                elif action == 'hover':
                    if not await self.browser_tool.hover(handle):
                        return done("Blocked", f"Could not hover: {target}")
                    res.actual_result = f"Hovered: {target}"
                elif action == 'scroll':
                    await self.browser_tool.scroll(handle)
                    res.actual_result = f"Scrolled to: {target}"
                elif action == 'upload':
                    path = self._asset_path(value or '')
                    if not await self.browser_tool.upload(handle, path):
                        return done("Blocked", f"Could not upload file to: {target}")
                    res.actual_result = f"Uploaded file to: {target}"
        except Exception as e:
            return done("Blocked", f"Step error: {str(e)[:150]}")

        if self.browser_tool.current_url().startswith('http') and \
                not self.url_validator.validate_url(self.browser_tool.current_url()):
            await self.browser_tool.go_back()
            return done("Blocked", "Navigation left the allow-listed domains and was reverted")

        extras = ctx.extra + self.browser_tool.dialog_messages
        corpus = await self.verification_agent.observe(extras)
        markers = app_cfg.get('account_markers', {})
        if any(_norm(m) in corpus for m in markers.get('created', [])) and ctx.generated_email and not ctx.created_seen:
            ctx.created_seen = True
            self.accounts.add(application, ctx.generated_email, ctx.data.get('password'), ctx.data.get('name'))
        if any(_norm(m) in corpus for m in markers.get('deleted', [])) and ctx.account and ctx.account.get('email'):
            self.accounts.mark_deleted(application, ctx.account['email'])

        return done("Pass", res.actual_result or "")

    @staticmethod
    def _literals_in(target: str, ctx: TestContext) -> List[str]:
        """Test-data values (product names, categories...) that the target names as whole words."""
        found = []
        for key, value in ctx.data.items():
            if len(value) >= 4 and not SENSITIVE_KEY.search(key) and '@' not in value \
                    and re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', target, re.I):
                found.append(value)
        return found

    async def _payment_refusal(self, handle, ctx: TestContext, app_cfg: Dict[str, Any]) -> Optional[str]:
        guard = self.apps_config.get('payment_guard', {})
        text = _norm(await self.browser_tool.element_text(handle))
        if not any(k in text for k in guard.get('keywords', [])):
            return None
        if not app_cfg.get('allow_test_payments'):
            return "Refused: this application does not permit submitting payments"
        cards = [re.sub(r'\D', '', v) for k, v in ctx.data.items() if 'card' in k and re.sub(r'\D', '', v)]
        if not cards or any(c not in guard.get('allowed_test_cards', []) for c in cards):
            return "Refused: payment can only be submitted with an approved test card number"
        return None

    @staticmethod
    def _asset_path(name: str) -> str:
        name = re.split(r'\s*\(', name)[0].strip() or 'sample.txt'
        path = Path('test_assets') / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Sample file for upload tests.\n", encoding='utf-8')
            logger.info(f"Created placeholder upload file {path}")
        return str(path.resolve())

    def _finish(self, tc: TestCase, application: str, environment: str, started: datetime, status: str,
                summary: str, step_results: List[StepResult], tokens: int,
                screenshot: Optional[str]) -> ExecutionResult:
        sources: Dict[str, int] = {}
        for r in step_results:
            if r.element_source != "None":
                sources[r.element_source] = sources.get(r.element_source, 0) + 1
        return ExecutionResult(
            run_id=self.run_id, test_case_id=tc.test_case_id, test_case_title=tc.test_case_title,
            application=application, environment=environment, status=status, actual_result=summary,
            screenshot_path=screenshot, steps=step_results,
            total_execution_time_ms=sum(r.execution_time_ms for r in step_results),
            total_tokens_used=tokens, element_sources=sources, row_index=tc.row_index,
            started_at=started, completed_at=datetime.now())

    # ------------------------------------------------------------------ reporting

    def _generate_run_summary(self, suite_name: str, application: str, environment: str,
                              start_time: datetime, end_time: datetime) -> RunSummary:
        results = self.execution_results
        steps = [s for r in results for s in r.steps]

        def count(items, status):
            return sum(1 for i in items if i.status == status)

        executed_steps = max(len(steps), 1)
        lookups = max(self.stats['lookups'], 1)
        served = self.stats['cache_hits'] + self.stats['fallback_hits']

        reviewed = {"approved": 0, "rejected": 0}
        with self.db_manager.get_connection() as conn:
            for row in conn.execute("SELECT decision, COUNT(*) AS n FROM locator_history "
                                    "WHERE decision IS NOT NULL GROUP BY decision"):
                if row['decision'] in reviewed:
                    reviewed[row['decision']] = row['n']
        reviewed_total = sum(reviewed.values())

        return RunSummary(
            run_id=self.run_id, suite_name=suite_name, application=application, environment=environment,
            start_time=start_time, end_time=end_time,
            total_duration_ms=(end_time - start_time).total_seconds() * 1000,
            total_test_cases=len(results), total_steps=len(steps),
            passed_test_cases=count(results, "Pass"), failed_test_cases=count(results, "Fail"),
            blocked_test_cases=count(results, "Blocked"), skipped_test_cases=count(results, "Skipped"),
            passed_steps=count(steps, "Pass"), failed_steps=count(steps, "Fail"),
            blocked_steps=count(steps, "Blocked"), skipped_steps=count(steps, "Skipped"),
            total_tokens_used=self.stats['total_tokens'], total_llm_calls=self.stats['llm_calls'],
            cache_hit_count=self.stats['cache_hits'], fallback_hit_count=self.stats['fallback_hits'],
            ai_identification_count=self.stats['ai_identifications'], healed_count=self.stats['heals'],
            cache_hit_rate=served / lookups if self.stats['lookups'] else 0.0,
            false_heal_rate=(reviewed['rejected'] / reviewed_total) if reviewed_total else 0.0,
            total_cost_usd=self.stats['total_cost'],
            cost_per_100_steps=self.stats['total_cost'] / executed_steps * 100)

    def _log_run_history(self, s: RunSummary):
        self.db_manager.save_run_history({
            'id': s.run_id, 'suite_name': s.suite_name, 'application': s.application,
            'environment': s.environment, 'start_time': s.start_time.isoformat(),
            'end_time': s.end_time.isoformat(), 'total_duration_ms': s.total_duration_ms,
            'total_test_cases': s.total_test_cases, 'total_steps': s.total_steps,
            'passed_test_cases': s.passed_test_cases, 'failed_test_cases': s.failed_test_cases,
            'blocked_test_cases': s.blocked_test_cases, 'skipped_test_cases': s.skipped_test_cases,
            'passed_steps': s.passed_steps, 'failed_steps': s.failed_steps,
            'blocked_steps': s.blocked_steps, 'skipped_steps': s.skipped_steps,
            'total_tokens_used': s.total_tokens_used, 'total_llm_calls': s.total_llm_calls,
            'cache_hit_count': s.cache_hit_count, 'fallback_hit_count': s.fallback_hit_count,
            'ai_identification_count': s.ai_identification_count, 'healed_count': s.healed_count,
            'cache_hit_rate': s.cache_hit_rate, 'false_heal_rate': s.false_heal_rate,
            'total_cost_usd': s.total_cost_usd, 'cost_per_100_steps': s.cost_per_100_steps})
        log_run_history(
            s.run_id, s.suite_name, s.application, s.environment, s.start_time, s.end_time,
            s.total_duration_ms,
            {'total': s.total_test_cases, 'passed': s.passed_test_cases, 'failed': s.failed_test_cases,
             'blocked': s.blocked_test_cases, 'skipped': s.skipped_test_cases},
            {'total': s.total_steps, 'passed': s.passed_steps, 'failed': s.failed_steps,
             'blocked': s.blocked_steps, 'skipped': s.skipped_steps},
            {'total_tokens': s.total_tokens_used, 'llm_calls': s.total_llm_calls,
             'cache_hits': s.cache_hit_count, 'cache_hit_rate': s.cache_hit_rate, 'heals': s.healed_count},
            {'total': s.total_cost_usd, 'per_100_steps': s.cost_per_100_steps})

    def _base_url(self, application: str, environment: str) -> str:
        app = self.apps_config.get('applications', {}).get(application, {})
        return app.get('environments', {}).get(environment, {}).get('url', '')


PLANNER_RULES = """Return JSON only, in exactly this shape:
{"steps": [{"action": "navigate|click|fill|select|hover|scroll|upload|press|wait|observe",
            "target": "<element description or null>", "value": "<literal or {{Placeholder}} or null>",
            "source_step": <number of the manual step>}],
 "checks": [{"text": "<exact on-screen string>", "after_step": <number in YOUR steps list>}]}

Rules:
1. The browser is already open on the application's home page at the start. Add navigate only for other URLs.
2. One atomic interaction per step. Expand a manual step into as many actions as it needs (for example
   'Fill Title, Password, DOB, First/Last name...' becomes one fill/select/click per field).
3. target describes the element as a user sees it: its visible text or label AND the section/form/card it belongs
   to, so it is unambiguous (e.g. "Email input in the sign-in form", "Delete button in the first table row").
   Never write selectors.
4. value of a fill or select step MUST be a {{Placeholder}} from AVAILABLE PLACEHOLDERS (or a literal that is quoted
   in the manual steps). Never invent data: if a manual step names a field (for example Title, newsletter, company)
   that has no test data, emit NO step for it. For select, the value is the option label. For upload, the value is
   the file name. For press, the value is the key. For scroll without a target, the value is top or bottom.
   Dates and card expiries are normally split across several fields: when AVAILABLE PLACEHOLDERS offers
   .day/.month/.year variants of a value, use them in separate steps (a date of birth is three dropdowns:
   {{DOB.day}}, {{DOB.month}}, {{DOB.year}}; a card expiry is two inputs: month and year).
   To scroll to the page bottom/footer or top, use action scroll with a null target and value bottom or top.
   Every distinct field needs its own target wording: when two fields share a label (expiry month and expiry year,
   birth day and month), name which one in the target.
5. If a step says to leave a field blank or empty, emit NO fill step for it.
6. Browser alerts are accepted automatically: add no step for them. Hover happens automatically before a click:
   use hover only when the manual step needs hovering without a click.
7. A manual step that only says 'Login' means: follow LOGIN METHOD, using {{account.email}} (also available as
   {{account.username}}) and {{account.password}} when the test data supplies no credentials.
8. Use observe for steps that only look at or verify the page.
9. checks: the strings that must be visible on screen according to the EXPECTED RESULT: copy every quoted message
   exactly, plus any other literal on-screen text it names (menu labels, headings, prices, product names). Each
   check text MUST be copied verbatim from the EXPECTED RESULT or the test data; do not paraphrase and do not
   include text that would not literally appear on a page. after_step is the step whose execution makes the text
   appear."""
