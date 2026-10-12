import asyncio
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, ElementHandle
from src.utils import get_logger


logger = get_logger(__name__)


_JS_HELPERS = """
const isVisible = (el) => {
  if (!el || !el.getBoundingClientRect) return false;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const s = getComputedStyle(el);
  return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0;
};
const norm = (t) => (t || '').replace(/\\s+/g, ' ').trim();
"""

_JS_LOCAL = """
const localText = (el) => {
  // the largest enclosing block of limited size: a product card or a detail panel, never the whole product grid
  let best = '';
  for (let p = el.parentElement, i = 0; i < 8 && p; i++, p = p.parentElement) {
    const t = norm(p.innerText);
    if (t.length > 500) break;
    if (t.length >= 15) best = t;
  }
  return best;
};
"""

_JS_SNAPSHOT = """
([maxElements, ctxAll]) => {
  %(helpers)s
  document.querySelectorAll('[data-ai-idx]').forEach(e => e.removeAttribute('data-ai-idx'));
  const sel = 'a, button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [role=menuitem], [onclick], summary, h1, h2, h3, h4';
  const nodes = [...document.querySelectorAll(sel)].filter(isVisible);
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return norm(el.labels[0].innerText).slice(0, 40);
    const w = el.closest('label');
    return w ? norm(w.innerText).slice(0, 40) : '';
  };
  const ctxOf = (el) => {
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const t = norm(p.innerText);
      if (t.length >= 15) return t.slice(0, 70);
    }
    return '';
  };
  const iconOf = (el) => {
    const c = el.querySelector('i, svg, img');
    if (!c) return '';
    const cls = (typeof c.className === 'string' ? c.className : (c.className && c.className.baseVal) || '').trim();
    return cls ? cls.split(/\\s+/).slice(0, 2).join(' ') : (c.getAttribute('alt') || '');
  };
  const rows = [];
  nodes.slice(0, maxElements).forEach((el, i) => {
    const tag = el.tagName.toLowerCase();
    const isBtnInput = tag === 'input' && ['submit', 'button', 'reset'].includes(el.type);
    const text = isBtnInput ? norm(el.value) : norm(el.innerText || '');
    const r = el.getBoundingClientRect();
    el.setAttribute('data-ai-idx', i);
    rows.push({
      n: i, tag, type: el.getAttribute('type') || undefined,
      text: text.slice(0, 60) || undefined,
      ico: text ? undefined : (iconOf(el) || undefined),
      title: el.getAttribute('title') || undefined,
      id: el.id || undefined, name: el.getAttribute('name') || undefined,
      ph: el.getAttribute('placeholder') || undefined,
      aria: el.getAttribute('aria-label') || undefined,
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 80) || undefined : undefined,
      tid: el.getAttribute('data-testid') || el.getAttribute('data-qa') || el.getAttribute('data-test') || undefined,
      lbl: ['input', 'select', 'textarea'].includes(tag) ? (labelOf(el) || undefined) : undefined,
      cls: (typeof el.className === 'string' && el.className) ? el.className.split(/\\s+/).slice(0, 3).join(' ') : undefined,
      y: Math.round(r.top + window.scrollY),
      _ctx: ctxOf(el)
    });
  });
  const keyOf = (r) => [r.tag, r.type, r.text, r.id, r.name, r.ph, r.href].join('|');
  const counts = {};
  rows.forEach(r => { const k = keyOf(r); counts[k] = (counts[k] || 0) + 1; });
  rows.forEach(r => { if (ctxAll || counts[keyOf(r)] > 1) r.ctx = r._ctx; delete r._ctx; });
  return rows;
}
""" % {"helpers": _JS_HELPERS}

_JS_BUILD_LOCATORS = """
(n) => {
  %(helpers)s
  %(local)s
  const el = document.querySelector('[data-ai-idx="' + n + '"]');
  if (!el) return null;
  const tag = el.tagName.toLowerCase();
  const q = (v) => '"' + String(v).replace(/"/g, '\\\\"') + '"';
  const visibleMatches = (spec) => {
    try {
      if (spec.startsWith('/') || spec.startsWith('(')) {
        const r = document.evaluate(spec, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const out = [];
        for (let i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i));
        return out.filter(isVisible);
      }
      return [...document.querySelectorAll(spec)].filter(isVisible);
    } catch (e) { return []; }
  };
  const isUnique = (spec) => { const m = visibleMatches(spec); return m.length === 1 && m[0] === el; };
  const cands = [];
  const add = (strategy, spec) => { if (spec && !cands.find(c => c.locator === spec) && isUnique(spec)) cands.push({strategy, locator: spec}); };

  ['data-testid', 'data-test', 'data-qa', 'data-cy'].forEach(a => {
    const v = el.getAttribute(a);
    if (v) add('testid', tag + '[' + a + '=' + q(v) + ']');
  });
  if (el.id && !/\\d{4,}/.test(el.id)) add('id', tag + '[id=' + q(el.id) + ']');
  const name = el.getAttribute('name');
  if (name) {
    add('name', tag + '[name=' + q(name) + ']');
    const val = el.getAttribute('value');
    if (val) add('name', tag + '[name=' + q(name) + '][value=' + q(val) + ']');
  }
  const ph = el.getAttribute('placeholder');
  if (ph) add('label', tag + '[placeholder=' + q(ph) + ']');
  const aria = el.getAttribute('aria-label');
  if (aria) add('label', tag + '[aria-label=' + q(aria) + ']');
  if (tag === 'a' && el.getAttribute('href')) add('href', 'a[href=' + q(el.getAttribute('href')) + ']');

  const isBtnInput = tag === 'input' && ['submit', 'button', 'reset'].includes(el.type);
  const text = isBtnInput ? norm(el.value) : norm(el.innerText || '');
  // accessible name through an associated <label>
  if (['input', 'select', 'textarea'].includes(tag) && el.labels && el.labels.length) {
    const lbl = norm(el.labels[0].innerText);
    if (lbl && lbl.length <= 60 && lbl.indexOf('"') < 0) {
      add('label', '//' + tag + '[@id=//label[normalize-space(.)=' + q(lbl) + ']/@for]');
    }
  }
  // explicit ARIA role plus accessible name
  const role = el.getAttribute('role');
  if (role && text && text.length <= 60 && text.indexOf('"') < 0) {
    add('role', '//*[@role=' + q(role) + '][normalize-space(.)=' + q(text) + ']');
  }
  let baseX = null;
  if (text && text.length <= 60 && text.indexOf('"') < 0) {
    baseX = '//' + tag + '[normalize-space(.)=' + q(text) + ']';
    add('text', baseX);
  }

  // css path anchored at the nearest ancestor with a stable id
  const cssPath = () => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      const t = cur.tagName.toLowerCase();
      if (cur.id && !/\\d{4,}/.test(cur.id) && cur !== el) { parts.unshift(t + '[id=' + q(cur.id) + ']'); break; }
      const sibs = [...cur.parentElement.children].filter(c => c.tagName === cur.tagName);
      parts.unshift(sibs.length > 1 ? t + ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')' : t);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  add('css', cssPath());

  if (baseX) {
    const r = document.evaluate(baseX, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    for (let i = 0; i < r.snapshotLength; i++) {
      if (r.snapshotItem(i) === el) { add('xpath', '(' + baseX + ')[' + (i + 1) + ']'); break; }
    }
  }
  const absX = () => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      const t = cur.tagName.toLowerCase();
      const sibs = [...cur.parentElement ? cur.parentElement.children : []].filter(c => c.tagName === cur.tagName);
      parts.unshift(sibs.length > 1 ? t + '[' + (sibs.indexOf(cur) + 1) + ']' : t);
      cur = cur.parentElement;
    }
    return '/' + parts.join('/');
  };
  add('xpath', absX());

  const r = el.getBoundingClientRect();
  const fp = {
    tag, text: text.slice(0, 80) || null, id: el.id || null, name: name || null,
    type: el.getAttribute('type') || null, placeholder: ph || null, aria_label: aria || null,
    href: tag === 'a' ? (el.getAttribute('href') || null) : null,
    data_testid: el.getAttribute('data-testid') || el.getAttribute('data-qa') || null,
    class_names: (typeof el.className === 'string' && el.className) ? el.className.split(/\\s+/).slice(0, 4) : null,
    position: {x: Math.round(r.left), y: Math.round(r.top + window.scrollY), width: Math.round(r.width), height: Math.round(r.height)}
  };
  let p = el.parentElement, ctx = '';
  for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
    const t = norm(p.innerText);
    if (t.length >= 15) { ctx = t.slice(0, 70); break; }
  }
  fp.context = ctx || null;
  fp.local = localText(el) || null;
  return {strategies: cands.slice(0, 7), fingerprint: fp};
}
""" % {"helpers": _JS_HELPERS, "local": _JS_LOCAL}

_JS_RESOLVE = """
(spec) => {
  %(helpers)s
  let nodes = [];
  try {
    if (spec.startsWith('/') || spec.startsWith('(')) {
      const r = document.evaluate(spec, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      for (let i = 0; i < r.snapshotLength; i++) nodes.push(r.snapshotItem(i));
    } else {
      nodes = [...document.querySelectorAll(spec)];
    }
  } catch (e) { return null; }
  const vis = nodes.filter(isVisible);
  return vis.length === 1 ? vis[0] : null;
}
""" % {"helpers": _JS_HELPERS}

_JS_FINGERPRINT = """
(el) => {
  %(helpers)s
  %(local)s
  const tag = el.tagName.toLowerCase();
  const isBtnInput = tag === 'input' && ['submit', 'button', 'reset'].includes(el.type);
  const text = isBtnInput ? norm(el.value) : norm(el.innerText || '');
  const r = el.getBoundingClientRect();
  let ctx = '';
  for (let p = el.parentElement, i = 0; i < 5 && p; i++, p = p.parentElement) {
    const t = norm(p.innerText);
    if (t.length >= 15) { ctx = t.slice(0, 70); break; }
  }
  return {tag, text: text.slice(0, 80) || null, context: ctx || null, local: localText(el) || null,
          id: el.id || null, name: el.getAttribute('name'),
          type: el.getAttribute('type'), placeholder: el.getAttribute('placeholder'),
          aria_label: el.getAttribute('aria-label'), href: tag === 'a' ? el.getAttribute('href') : null,
          position: {x: Math.round(r.left), y: Math.round(r.top + window.scrollY), width: Math.round(r.width), height: Math.round(r.height)}};
}
""" % {"helpers": _JS_HELPERS, "local": _JS_LOCAL}

_JS_VALIDATION = """
(el) => {
  %(helpers)s
  const f = el.closest('form');
  if (!f) return [];
  return [...f.querySelectorAll('input, select, textarea')]
    .filter(i => i.willValidate && !i.checkValidity() && isVisible(i))
    .map(i => i.validationMessage).filter(Boolean);
}
""" % {"helpers": _JS_HELPERS}


class BrowserTool:
    def __init__(self, headless: bool = True, block_url_patterns: Optional[List[str]] = None,
                 min_interval_s: float = 0.0, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self.block_url_patterns = [p.lower() for p in (block_url_patterns or [])]
        self.min_interval_s = min_interval_s
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.dialog_messages: List[str] = []
        self._last_action = 0.0

    def configure(self, block_url_patterns: Optional[List[str]], min_interval_s: float):
        self.block_url_patterns = [p.lower() for p in (block_url_patterns or [])]
        self.min_interval_s = min_interval_s

    async def initialize(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)

    async def new_context(self):
        """Start every test case from a clean, logged-out browser state."""
        await self.close_context()
        self.context = await self.browser.new_context(
            viewport={'width': 1366, 'height': 800}, locale='en-US')
        if self.block_url_patterns:
            async def _route(route):
                if any(p in route.request.url.lower() for p in self.block_url_patterns):
                    await route.abort()
                else:
                    await route.continue_()
            await self.context.route("**/*", _route)
        self.page = await self.context.new_page()
        self.dialog_messages = []

        async def _on_dialog(dialog):
            self.dialog_messages.append(dialog.message)
            await dialog.accept()
        self.page.on("dialog", lambda d: asyncio.ensure_future(_on_dialog(d)))

        def _on_request_failed(request):
            url = request.url.lower()
            failure = str(request.failure or '')
            if 'mixed-content' in failure or 'ERR_ABORTED' in failure:
                return  # third-party fonts over http and cancelled navigations are not test-relevant
            if not any(p in url for p in self.block_url_patterns):
                logger.warning(f"Request failed: {request.url[:120]} ({failure})")
        self.page.on("requestfailed", _on_request_failed)

    async def close_context(self):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None

    async def close(self):
        await self.close_context()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def _throttle(self):
        wait = self.min_interval_s - (time.monotonic() - self._last_action)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_action = time.monotonic()

    async def navigate_to(self, url: str, timeout: int = 30000) -> bool:
        try:
            await self._throttle()
            await self.page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            await self.settle()
            logger.info(f"Navigated to {url}")
            return True
        except Exception as e:
            logger.error(f"Failed to navigate to {url}: {str(e)[:150]}")
            return False

    async def go_back(self):
        try:
            await self.page.go_back(wait_until='domcontentloaded', timeout=10000)
        except Exception:
            pass

    def current_url(self) -> str:
        return self.page.url if self.page else ""

    async def settle(self, timeout_ms: int = 4000):
        """Wait for the page, its stylesheets and fonts to load so the snapshot reflects what a user sees."""
        try:
            await self.page.wait_for_load_state('load', timeout=12000)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state('networkidle', timeout=timeout_ms)
        except Exception:
            pass

    # ---------- page observation ----------

    async def visible_text(self) -> str:
        try:
            # innerText leaves out the label of <input type=submit|button>, which lives in its value attribute
            body = await self.page.evaluate("""() => {
                const text = document.body ? document.body.innerText : '';
                const labels = [...document.querySelectorAll('input[type=submit], input[type=button], input[type=reset]')]
                    .filter(i => i.offsetParent !== null).map(i => i.value).filter(Boolean);
                return text + '\\n' + labels.join('\\n');
            }""")
            title = await self.page.title()
            return f"{title}\n{body}"
        except Exception:
            return ""

    async def snapshot_elements(self, max_elements: int = 350, ctx_all: bool = False) -> List[Dict[str, Any]]:
        try:
            return await self.page.evaluate(_JS_SNAPSHOT, [max_elements, ctx_all])
        except Exception as e:
            logger.error(f"DOM snapshot failed: {str(e)[:150]}")
            return []

    async def build_locators(self, index: int) -> Optional[Dict[str, Any]]:
        try:
            return await self.page.evaluate(_JS_BUILD_LOCATORS, index)
        except Exception as e:
            logger.error(f"Locator build failed: {str(e)[:150]}")
            return None

    async def clear_snapshot_marks(self):
        try:
            await self.page.evaluate("() => document.querySelectorAll('[data-ai-idx]').forEach(e => e.removeAttribute('data-ai-idx'))")
        except Exception:
            pass

    # ---------- element resolution ----------

    async def resolve(self, selector: str) -> Optional[ElementHandle]:
        """Return the element only if the selector matches exactly one visible element."""
        try:
            handle = await self.page.evaluate_handle(_JS_RESOLVE, selector)
            return handle.as_element()
        except Exception:
            return None

    async def fingerprint(self, element: ElementHandle) -> Dict[str, Any]:
        try:
            return await element.evaluate(_JS_FINGERPRINT)
        except Exception:
            return {}

    async def element_text(self, element: ElementHandle) -> str:
        try:
            return (await element.inner_text()) or ""
        except Exception:
            return ""

    async def validation_messages(self, element: ElementHandle) -> List[str]:
        try:
            return await element.evaluate(_JS_VALIDATION)
        except Exception:
            return []

    # ---------- actions ----------

    async def click(self, element: ElementHandle) -> bool:
        await self._throttle()
        try:
            await element.scroll_into_view_if_needed(timeout=5000)
            try:
                await element.click(timeout=8000)
            except Exception:
                await element.hover(timeout=3000)
                await element.click(timeout=5000, force=True)
            return True
        except Exception as e:
            try:
                await element.evaluate("e => e.click()")
                return True
            except Exception:
                logger.error(f"Click failed: {str(e)[:150]}")
                return False

    async def fill(self, element: ElementHandle, value: str) -> bool:
        try:
            await element.scroll_into_view_if_needed(timeout=5000)
            await element.fill(value, timeout=6000)
            return True
        except Exception as e:
            logger.error(f"Fill failed: {str(e)[:150]}")
            return False

    async def select(self, element: ElementHandle, value: str) -> bool:
        try:
            if (await element.evaluate("e => e.tagName")).lower() != 'select':
                return await self.fill(element, value)  # a "dropdown" that is really a text input
            await element.select_option(label=value, timeout=5000)
            return True
        except Exception:
            try:
                await element.select_option(value=value, timeout=3000)
                return True
            except Exception as e:
                logger.error(f"Select failed: {str(e)[:150]}")
                return False

    async def hover(self, element: ElementHandle) -> bool:
        try:
            await element.scroll_into_view_if_needed(timeout=5000)
            await element.hover(timeout=5000)
            return True
        except Exception as e:
            logger.error(f"Hover failed: {str(e)[:150]}")
            return False

    async def upload(self, element: ElementHandle, path: str) -> bool:
        try:
            await element.set_input_files(path, timeout=5000)
            return True
        except Exception as e:
            logger.error(f"Upload failed: {str(e)[:150]}")
            return False

    async def press(self, key: str) -> bool:
        try:
            await self.page.keyboard.press(key)
            return True
        except Exception as e:
            logger.error(f"Key press failed: {str(e)[:150]}")
            return False

    async def scroll(self, element: Optional[ElementHandle], to: str = "bottom") -> bool:
        try:
            if element:
                await element.scroll_into_view_if_needed(timeout=5000)
            elif to == "top":
                await self.page.evaluate("() => window.scrollTo(0, 0)")
            else:
                await self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            return True
        except Exception as e:
            logger.error(f"Scroll failed: {str(e)[:150]}")
            return False

    async def take_screenshot(self, path: str) -> bool:
        """Screenshot with every input and textarea masked so typed credentials and personal data never appear."""
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=path, mask=[self.page.locator("input, textarea")])
            return True
        except Exception as e:
            logger.error(f"Screenshot failed: {str(e)[:150]}")
            return False
