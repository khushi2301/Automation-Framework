# Assignment 4 requirements mapped to the implementation

Status: **Met** = implemented and exercised on the live site; **Partial** = implemented with a stated gap.

## Element resolution flow

| Step in the assignment | Where | Status |
|---|---|---|
| Cache lookup keyed by application, page, target description; exactly one visible element matching the stored fingerprint; zero tokens | `locator_agent.resolve_element`, `_try_reference`, `_fingerprint_matches` | Met |
| Deterministic fallback: other stored strategies and fingerprint matching, zero tokens | strategies built in `browser_tool._JS_BUILD_LOCATORS`: test id, id, name, label (attribute and `<label for>`), role + name, href, text, CSS, XPath | Met |
| AI identification from a compact page representation; verify the answer deterministically | `_ai_identify`: filtered list of visible interactive elements, the LLM returns an index, locators are built and checked by the framework | Met (filtered DOM; no screenshot or accessibility tree) |
| Store or heal with several strategies and a fingerprint (tag, text, key attributes, position); version history; mark Healed | `locator_store_tool.create_locator` / `heal_locator`, tables `locator_reference` and `locator_history` | Met |
| Refuse to guess: Blocked with the reason | confidence threshold, lookalike guard, compatibility check on heals | Met |

## Core requirements

| Requirement | Implementation | Status |
|---|---|---|
| Excel-driven execution, only rows marked Y, results written back without breaking formatting | `excel_tool.ReadExcelTool` / `WriteExcelTool`: result columns, `Step Results` and `Run Summary` sheets; styles of existing cells untouched; re-runs overwrite instead of adding columns | Met |
| Element reference store: persistent, versioned, per application and environment, inspectable, exportable, invalidation of entries and pages | SQLite `data/framework.db`; `--list-locators`, `--export-locators`, `--import-locators`, `--invalidate-locator`, `--invalidate-page`, `--invalidate-all` | Met |
| Zero-token replay; any token use on a repeat run explained in the log | test plans are stored (`test_plans` table) as well as elements; every LLM call is logged with reason, test case and step | Met: runs 2 and 3 used 0 tokens |
| Self-healing with review: healed elements flagged in results and report; approve or reject; optional auto-approval | `Healed` element source in Excel, per-heal section in `reports/`, `--list-heals`, `--approve-heal`, `--reject-heal`, `framework.auto_approve_heals` | Met |
| Verification integrity: AI never changes an expected result or turns a Fail into a Pass | `verification_agent`: deterministic text matching of workbook text; the LLM is never given the verdict; Fail is never healed | Met (see limits in the README) |
| Reusable framework: no application-specific code in the core; details in configuration; demonstrated on a second application | `config/applications.yaml` (login method, registration test case or fixed test account, account markers, planner hints, ad blocking, payment policy); second application `sauce_demo` run with no core change; its twelve-case suite is in `second_application/` | Partial: a five-case version of the suite passed and replayed at 0 tokens; the project's twelve-case workbook has not been run yet and needs its learning run and replays (blocked by exhausted LLM credits) |
| Token and usage logging; run history; compatible with the Assignment 2 dashboard format | `logs/llm_usage.csv`, `logs/run_history.csv`, tables `llm_usage` and `run_history`; token counts come from the API usage fields. The log carries every field the Assignment 2 dashboard lists (`timestamp`, `user_id`, `agent_name`, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `status`, `cost_usd`) plus run, test case, step and reason | Met. Reconciled for run `run-ffe383c9`: 93 calls, 288,575 tokens, $3.0556 in the CSV, in the `llm_usage` table and in the run summary alike. The Assignment 2 dashboard itself is not part of this project, so loading this log into it as a separate tab has not been tried |
| Safe use: allow-list, no real payments, provided test accounts, rate limits, masking | `allowed_domains` enforced on start and after every step; `payment_guard`; test accounts only; 1 request per second; `mask_sensitive` for logs and LLM prompts; screenshots mask inputs | Met |
| Packaging: installable or Docker, runnable from a command line | `pyproject.toml` (installs the `selfheal-run` command; installed and run in a clean virtualenv), `Dockerfile` + `.dockerignore`, `python main.py ...` | Met for the pip package; the Dockerfile is written but was not built (no Docker on the dev machine) |

## Evaluation items

| Item | Result |
|---|---|
| Token reduction | run 1: 288,575; run 2: 0; run 3: 0 |
| Cache hit rate on repeat runs | 100% (cache + fallback hits / element lookups) |
| First-run identification accuracy | every element used in the 20 passing tests came from the LLM or the store; one lookalike mis-pick (a near-identically named product) was seen during development and is now rejected by the guard. A manual sample check against the screenshots is still advisable |
| Healing success / false heal rate | a deliberately broken locator was healed correctly. During development three false heal candidates (a transient modal mistaken for a changed element) were rejected in review; the wait-before-heal rule removed them, and the final three runs had 0 heals |
| Verification integrity | a row whose expected text is absent from the page ends as Fail while a control row in the same run passes |
| Stability | identical results (20 pass) in all three runs |
| Speed and cost | learning run 623 s and an estimated $3.06 (about $2.57 per 100 steps); repeat runs about 210-230 s and $0 |
| Portability | a five-case suite on saucedemo.com passed (replayed at 0 tokens) using only a configuration entry |
