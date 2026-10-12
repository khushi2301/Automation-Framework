import os
import re
import json
import time
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from src.models import LLMUsage, LocatorFingerprint, Locator
from src.storage import DatabaseManager
from src.tools import BrowserTool, LocatorStoreTool
from src.utils import get_logger, log_llm_usage, mask_sensitive


logger = get_logger(__name__)

_FIELD_TAGS = {'input', 'textarea', 'select'}
_CLICK_TAGS = {'a', 'button'}


@dataclass
class Resolution:
    handle: Any = None
    source: str = "None"          # Cache | Fallback | AI | Healed | None
    tokens: int = 0
    reason: Optional[str] = None  # why the element could not be identified (Blocked)


def page_key(url: str) -> str:
    """Stable page identity: URL path with numeric segments generalised (e.g. /product_details/12 -> /product_details/{n})."""
    path = urlparse(url).path or "/"
    path = re.sub(r'/\d+', '/{n}', path)
    return path.rstrip('/') or "/"


class LocatorAgent:
    def __init__(self, config: Dict[str, Any], db_manager: DatabaseManager,
                 browser_tool: BrowserTool, stats: Dict[str, Any]):
        self.config = config
        self.db_manager = db_manager
        self.browser_tool = browser_tool
        self.stats = stats
        self.locator_store = LocatorStoreTool(db_manager)
        self.run_id = "no-run"
        self.client = None
        self.llm_error: Optional[str] = None
        llm_cfg = config.get('llm', {})
        self.model = llm_cfg.get('model', 'gpt-4-turbo')
        self.price_in = llm_cfg.get('pricing', {}).get('input_per_1k', 0.01)
        self.price_out = llm_cfg.get('pricing', {}).get('output_per_1k', 0.03)
        self.min_confidence = config.get('framework', {}).get('ai_confidence_threshold', 0.6)
        self.auto_approve = config.get('framework', {}).get('auto_approve_heals', False)
        self._init_client()

    def _init_client(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("OPENAI_API_KEY not set: only stored references can be used, new elements will be Blocked")
            return
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
            logger.info("LLM client initialised")
        except Exception as e:
            logger.warning(f"LLM client initialisation failed: {str(e)[:100]}")

    # ---------- LLM access with full usage logging ----------

    def llm_json(self, system: str, user: str, test_case_id: str, step_no: int,
                 agent_name: str, reason: str, max_tokens: int) -> Tuple[Optional[Dict[str, Any]], int]:
        if not self.client or self.llm_error:
            return None, 0
        user = mask_sensitive(user)
        error = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.1, max_tokens=max_tokens,
                    response_format={"type": "json_object"}
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                in_tok = response.usage.prompt_tokens
                out_tok = response.usage.completion_tokens
                cost = in_tok / 1000 * self.price_in + out_tok / 1000 * self.price_out
                self._record_usage(test_case_id, step_no, agent_name, reason, in_tok, out_tok,
                                   latency_ms, cost, "success", None)
                try:
                    return json.loads(response.choices[0].message.content), in_tok + out_tok
                except json.JSONDecodeError:
                    logger.warning(f"LLM returned invalid JSON for {agent_name}")
                    return None, in_tok + out_tok
            except Exception as e:
                full_error = str(e)
                error = full_error[:200]
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._record_usage(test_case_id, step_no, agent_name, reason, 0, 0,
                                   latency_ms, 0.0, "error", error)
                if 'insufficient_quota' in full_error or 'no credits remaining' in full_error:
                    self.llm_error = "OpenAI quota exhausted (insufficient_quota): add credits to the account"
                elif 'invalid_api_key' in full_error or 'Incorrect API key' in full_error:
                    self.llm_error = "OpenAI API key was rejected"
                if self.llm_error:
                    logger.error(f"{self.llm_error}. No further LLM calls will be attempted in this run; "
                                 f"steps that need the LLM will be Blocked, stored references still replay.")
                    return None, 0
                time.sleep(2)
        logger.error(f"LLM call failed for {agent_name}: {error}")
        return None, 0

    def _record_usage(self, test_case_id, step_no, agent_name, reason, in_tok, out_tok,
                      latency_ms, cost, status, error):
        user_id = os.getenv('USERNAME') or os.getenv('USER') or 'unknown'
        log_llm_usage(self.run_id, test_case_id, step_no, user_id, agent_name, reason, self.model,
                      in_tok, out_tok, latency_ms, cost, status, error)
        try:
            self.db_manager.log_llm_usage(LLMUsage(
                id=f"usage-{uuid.uuid4().hex[:10]}", run_id=self.run_id, test_case_id=test_case_id,
                step_no=step_no, user_id=user_id, agent_name=agent_name, reason=reason,
                model=self.model, input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency_ms,
                cost_usd=cost, status=status, error_message=error, timestamp=datetime.now()))
        except Exception as e:
            logger.warning(f"Could not persist LLM usage row: {str(e)[:100]}")
        self.stats['llm_calls'] += 1
        self.stats['total_tokens'] += in_tok + out_tok
        self.stats['total_cost'] += cost

    # ---------- element resolution ----------

    async def resolve_element(self, application: str, environment: str, test_case_id: str,
                              step_no: int, target: str, action: str,
                              must_contain: Optional[List[str]] = None) -> Resolution:
        """must_contain: literal words from the test data that the target names (e.g. a product name). The element
        chosen, cached or AI-picked, must carry them in its own text or its surrounding text, so a similar-looking
        neighbour (another product with a near-identical name) can never be accepted."""
        must_contain = must_contain or []
        page = page_key(self.browser_tool.current_url())
        refs = self.locator_store.get_references(application, environment, page, target)
        approved, pending = refs['approved'], refs['pending']
        self.stats['lookups'] += 1

        # Stage 1 (cache) and stage 2 (deterministic fallback): zero tokens. A stored element that is missing may
        # only be mid-animation or waiting on an ajax call, so give it a few seconds before treating it as changed;
        # healing must only follow a persistent absence, never a transient page state.
        deadline = time.monotonic() + (4.0 if (approved or pending) else 0.0)
        while True:
            for ref in (approved, pending):
                if not ref:
                    continue
                hit = await self._try_reference(ref, must_contain)
                if hit:
                    handle, source = hit
                    self.stats['cache_hits' if source == 'Cache' else 'fallback_hits'] += 1
                    logger.info(f"{source} hit for '{target}' on {page}")
                    return Resolution(handle, source, 0)
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)

        # Stage 3: AI identification, verified deterministically before use
        previous = approved or pending
        resolution = await self._ai_identify(application, environment, test_case_id, step_no,
                                             target, action, page, previous, must_contain=must_contain)
        return resolution

    async def _try_reference(self, ref: Locator, must_contain: List[str]) -> Optional[Tuple[Any, str]]:
        candidates = [(ref.primary_locator, 'Cache')] + [(f['locator'], 'Fallback') for f in ref.fallback_locators]
        for selector, source in candidates:
            handle = await self.browser_tool.resolve(selector)
            if handle:
                current = await self.browser_tool.fingerprint(handle)
                if self._fingerprint_matches(ref.fingerprint, current) and self._carries(current, must_contain):
                    return handle, source
        return None

    @staticmethod
    def _carries(info: Dict[str, Any], must_contain: List[str]) -> bool:
        """True if the element's own text or surrounding text contains every required literal (whole words)."""
        if not must_contain:
            return True
        hay = re.sub(r'\s+', ' ', ' '.join(str(info.get(k) or '') for k in
                                           ('text', 'context', 'local', 'href', 'name', 'aria_label'))).lower()
        return all(re.search(r'(?<!\w)' + re.escape(re.sub(r'\s+', ' ', m).strip().lower()) + r'(?!\w)', hay)
                   for m in must_contain)

    @staticmethod
    def _fingerprint_matches(stored: LocatorFingerprint, current: Dict[str, Any]) -> bool:
        if not current or stored.tag != current.get('tag'):
            return False
        for attr in ('id', 'name', 'type'):
            wanted = getattr(stored, attr)
            if wanted and current.get(attr) != wanted:
                return False
        if stored.text:
            a = re.sub(r'\s+', ' ', stored.text).strip().lower()
            b = re.sub(r'\s+', ' ', current.get('text') or '').strip().lower()
            if a != b and a not in b and b not in a and SequenceMatcher(None, a, b).ratio() < 0.6:
                return False
        return True

    async def _ai_identify(self, application: str, environment: str, test_case_id: str, step_no: int,
                           target: str, action: str, page: str, previous: Optional[Locator],
                           retry_of: Optional[str] = None, carried_tokens: int = 0,
                           must_contain: Optional[List[str]] = None,
                           avoid: Optional[List[int]] = None) -> Resolution:
        must_contain = must_contain or []
        avoid = avoid or []
        if not self.client:
            return Resolution(reason="No stored reference and no LLM available")
        if self.llm_error:
            return Resolution(reason=f"No stored reference and the LLM is unavailable: {self.llm_error}")

        elements = await self.browser_tool.snapshot_elements(ctx_all=bool(must_contain))
        if not elements:
            return Resolution(reason="Page has no visible interactive elements")

        compact = "\n".join(json.dumps({k: v for k, v in e.items() if v not in (None, '')},
                                       separators=(',', ':'), ensure_ascii=False) for e in elements)
        if retry_of is not None and compact == retry_of:
            await self.browser_tool.clear_snapshot_marks()
            return Resolution(tokens=carried_tokens, reason="LLM could not identify element (page unchanged on retry)")
        healing_note = ""
        if previous:
            fp = previous.fingerprint
            healing_note = (f"\nThis element was identified before but can no longer be found. Previous fingerprint: "
                            f"tag={fp.tag}, text={fp.text!r}, id={fp.id!r}, name={fp.name!r}, context={fp.context!r}. "
                            f"Pick the element that now plays the same role, or null if it no longer exists.")
        constraint = ""
        if must_contain:
            constraint += ("\nThe chosen element's own text or its ctx MUST contain these exact words: "
                           + ", ".join(f"'{m}'" for m in must_contain)
                           + ". Near-identical names are different items: do not pick a lookalike.")
        if avoid:
            constraint += f"\nDo NOT choose n in {avoid}: they were checked and rejected."
        user = (f"TARGET ELEMENT: {target}\nACTION TO PERFORM ON IT: {action}\n"
                f"PAGE: {page}\n{healing_note}{constraint}\n\n"
                f"VISIBLE INTERACTIVE ELEMENTS (JSON, in DOM order; n = index, ctx = surrounding text, "
                f"lbl = form label, tid = test id, ico = icon class inside an element that has no text, "
                f"cls = css classes). Elements with no text are usually icon buttons: judge them by ico, cls, "
                f"title, href and ctx:\n{compact}\n\n"
                'Reply with JSON only: {"index": <n or null>, "confidence": <0..1>, "reason": "<short>"}.\n'
                "Choose the single element a user would act on to satisfy TARGET ELEMENT. "
                "If several look alike, use ctx, label and DOM order to pick the right one. "
                "If nothing clearly matches, set index to null and confidence to 0. Never guess.")
        system = ("You identify one element on a web page for a test automation framework. "
                  "You only choose from the elements listed; you never invent selectors.")

        result, tokens = self.llm_json(system, user, test_case_id, step_no, "LocatorAgent",
                                       "healing" if previous else "first_learning", max_tokens=200)
        tokens += carried_tokens
        self.stats['ai_identifications'] += 1

        if not result or result.get('index') is None:
            await self.browser_tool.clear_snapshot_marks()
            self._log_miss(test_case_id, step_no, target, page, result, compact)
            if retry_of is None:
                # the page may still have been rendering: look once more, and only ask again if it changed
                await self.browser_tool.settle()
                await asyncio.sleep(1.5)
                return await self._ai_identify(application, environment, test_case_id, step_no, target, action,
                                               page, previous, retry_of=compact, carried_tokens=tokens,
                                               must_contain=must_contain, avoid=avoid)
            return Resolution(tokens=tokens, reason=f"LLM could not identify element: {(result or {}).get('reason', 'no answer')}")

        confidence = float(result.get('confidence') or 0)
        index = result.get('index')
        if confidence < self.min_confidence or not isinstance(index, int) or not (0 <= index < len(elements)):
            await self.browser_tool.clear_snapshot_marks()
            return Resolution(tokens=tokens, reason=f"Refused to guess: confidence {confidence:.2f} below "
                                                    f"{self.min_confidence} ({result.get('reason', '')[:120]})")

        built = await self.browser_tool.build_locators(index)
        await self.browser_tool.clear_snapshot_marks()
        if not built or not built.get('strategies'):
            return Resolution(tokens=tokens, reason="Could not build a unique, visible locator for the chosen element")

        # Deterministic verification of the AI answer before it is used or stored
        handle = None
        for strategy in built['strategies']:
            handle = await self.browser_tool.resolve(strategy['locator'])
            if handle:
                break
        if not handle:
            return Resolution(tokens=tokens, reason="AI-chosen element did not resolve to exactly one visible element")

        if not self._carries(built['fingerprint'], must_contain):
            logger.warning(f"AI chose a lookalike for '{target}' (n={index}); rejecting it")
            if len(avoid) < 2:
                return await self._ai_identify(application, environment, test_case_id, step_no, target, action,
                                               page, previous, carried_tokens=tokens, must_contain=must_contain,
                                               avoid=avoid + [index])
            return Resolution(tokens=tokens, reason=f"Refused to guess: no element whose text contains "
                                                    f"{', '.join(repr(m) for m in must_contain)}")

        new_tag = built['fingerprint']['tag']
        if previous and not self._compatible(previous.fingerprint.tag, new_tag):
            return Resolution(tokens=tokens, reason=f"Heal refused: candidate <{new_tag}> is not compatible with "
                                                    f"stored <{previous.fingerprint.tag}>")

        if previous:
            self.locator_store.heal_locator(previous, built['strategies'], built['fingerprint'], self.auto_approve)
            self.stats['heals'] += 1
            source = "Healed"
        else:
            self.locator_store.create_locator(application, environment, page, target,
                                              built['strategies'], built['fingerprint'])
            source = "AI"
        logger.info(f"{source}: '{target}' -> <{new_tag}> via {built['strategies'][0]['strategy']} "
                    f"(confidence {confidence:.2f}, {tokens} tokens)")
        return Resolution(handle, source, tokens)

    def _log_miss(self, test_case_id: str, step_no: int, target: str, page: str,
                  result: Optional[Dict[str, Any]], offered: str):
        """Keep what the LLM was shown when it could not identify an element, so a Blocked step can be explained."""
        try:
            path = os.path.join("logs", "ai_misses.log")
            os.makedirs("logs", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} {self.run_id} {test_case_id} step {step_no} "
                        f"page={page}\nTARGET: {target}\nANSWER: {result}\nOFFERED ELEMENTS:\n{mask_sensitive(offered)}\n")
        except Exception:
            pass

    @staticmethod
    def _compatible(old_tag: str, new_tag: str) -> bool:
        if old_tag == new_tag:
            return True
        return (old_tag in _FIELD_TAGS and new_tag in _FIELD_TAGS) or \
               (old_tag in _CLICK_TAGS and new_tag in _CLICK_TAGS)
