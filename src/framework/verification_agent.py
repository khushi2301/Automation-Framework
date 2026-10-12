import re
import asyncio
from typing import Dict, Any, List, Tuple
from src.tools import BrowserTool
from src.utils import get_logger


logger = get_logger(__name__)

_QUOTE_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])'([^'\n]{2,150}?)'(?![A-Za-z0-9])"),
    re.compile(r'"([^"\n]{2,150}?)"'),
    re.compile(r'‘([^’\n]{2,150}?)’'),
    re.compile(r'“([^”\n]{2,150}?)”'),
]
_DEFAULT_AMOUNT = r'(?:Rs\.?|INR|USD|EUR|GBP|AED|[$€£₹])\s?\d[\d,]*(?:\.\d+)?'


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().lower()


class VerificationAgent:
    """Deterministic verification. Expected results come from the workbook and are never modified, softened or
    interpreted by an LLM: every literal phrase must be observed on the page or the test fails."""

    def __init__(self, config: Dict[str, Any], browser_tool: BrowserTool):
        self.config = config
        self.browser_tool = browser_tool
        self.amount = re.compile(config.get('verification', {}).get('amount_pattern', _DEFAULT_AMOUNT),
                                 re.IGNORECASE)

    def is_amount(self, text: str) -> bool:
        return bool(self.amount.fullmatch((text or '').strip()))

    def mandatory_phrases(self, expected_result: str) -> List[str]:
        """Every quoted message and every currency amount stated in the expected result must be observed."""
        phrases: List[str] = []
        for pattern in _QUOTE_PATTERNS:
            phrases.extend(m.strip() for m in pattern.findall(expected_result or ''))
        phrases.extend(m.group(0).strip() for m in self.amount.finditer(expected_result or ''))
        seen, unique = set(), []
        for p in phrases:
            if _norm(p) not in seen:
                seen.add(_norm(p))
                unique.append(p)
        return unique

    @staticmethod
    def grounded(phrase: str, *sources: str) -> bool:
        """A phrase is accepted only if it appears verbatim in the workbook text it claims to come from."""
        p = _norm(phrase)
        return bool(p) and any(p in _norm(s) for s in sources)

    @staticmethod
    def present(phrase: str, corpus: str) -> bool:
        phrase = phrase.split('<')[0]  # an unresolved <placeholder> only constrains the text before it
        needle = _norm(phrase).strip(' .!?:')
        return bool(needle) and needle in corpus

    async def observe(self, extra_messages: List[str]) -> str:
        text = await self.browser_tool.visible_text()
        return _norm(text + "\n" + "\n".join(extra_messages))

    async def wait_for(self, phrases: List[str], extra_messages: List[str],
                       timeout_s: float = 6.0) -> Tuple[List[str], List[str]]:
        """Poll the page until every phrase is observed or the timeout passes. Returns (found, missing)."""
        waited = 0.0
        while True:
            corpus = await self.observe(extra_messages)
            missing = [p for p in phrases if not self.present(p, corpus)]
            if not missing or waited >= timeout_s:
                return [p for p in phrases if p not in missing], missing
            await asyncio.sleep(0.4)
            waited += 0.4
