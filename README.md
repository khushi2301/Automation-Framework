# AI-Powered Self-Healing Test Automation Framework

Learn once, replay for free, heal only when needed.

The framework reads manual test cases from Excel, runs them in a real browser, and writes results back to the same
workbook. On the first run an LLM identifies the elements; every identified element is stored with several locator
strategies and a fingerprint. Repeat runs on an unchanged application use no LLM tokens. The LLM is called again only
when a stored element can no longer be found, and that heal waits for human review.

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium
# put OPENAI_API_KEY=... in a .env file (see .env.example)

python main.py AI_Assignment_Test_Cases.xlsx --application automation_exercise --environment production
```

The browser is visible by default; add `--headless` to hide it.

### Install as a package

```bash
pip install .                      # installs the `selfheal-run` command (same options as main.py)
playwright install chromium
selfheal-run AI_Assignment_Test_Cases.xlsx --application automation_exercise --environment production
```

It runs from any folder: the default `config/` files ship inside the package. To use your own, put a `config/` folder
in the working directory (it takes priority) or point at files with `--config` and `--applications-config`. `OPENAI_API_KEY` is read from the environment or from a `.env` file in the working
directory or any parent folder.

### Run in Docker

```bash
docker build -t selfheal .
docker run --rm --ipc=host --env-file .env \
  -v "$PWD:/work" -v "$PWD/data:/app/data" -v "$PWD/reports:/app/reports" -v "$PWD/logs:/app/logs" \
  selfheal /work/AI_Assignment_Test_Cases.xlsx --application automation_exercise --environment production
```

On Windows PowerShell replace `$PWD` with `${PWD}`. The container always runs the browser headless, and `--ipc=host`
stops Chromium crashing on the container's small shared memory. Mount `data/` so learned references, plans and test
accounts survive between runs (that is what makes repeat runs free). Quick check that the image works without an API
key: `docker run --rm selfheal` prints the command's help, and with your existing `data/` mounted a replay of the
provided suite uses no tokens.

| Option | Meaning |
|---|---|
| `--only TC_001,TC_004` | run just these IDs (they must still be marked `Y`) |
| `--replan` | discard the stored plans of the selected tests and plan them again (uses tokens) |
| `--suite-name NAME` | name shown in the run summary and history |
| `--slow-mo 600` | demo aid: wait this many milliseconds between browser actions |
| `--hold 4` | demo aid: keep the last page of each test open this many seconds, so a final error or message can be read |

### Reference store commands

```bash
python main.py --application A --environment E --list-locators
python main.py --application A --environment E --export-locators refs.json     # also --import-locators
python main.py --application A --environment E --invalidate-page /login        # or --invalidate-all
python main.py --invalidate-locator loc-1a2b3c4d
python main.py --application A --environment E --list-heals                    # healed references awaiting review
python main.py --approve-heal loc-1a2b3c4d --reviewer alice                    # or --reject-heal
```

## Excel format

One row per test case. Columns (names are matched case-insensitively):
`TestCase ID`, `Title`, `Test Step` (numbered lines), `Test Data` (`Key: value` lines), `Expected Result`,
`Execute` (`Y` runs, anything else is reported as Skipped), `Execution Result`. Extra columns are allowed.

After a run the same workbook receives, per row: Status (in `Execution Result`), Actual Result, Screenshot Path,
Execution Time (ms), Element Source (Cache / Fallback / AI / Healed counts), Tokens Used, Run ID, Executed At.
A `Step Results` sheet lists every atomic step and a `Run Summary` sheet holds the totals. If the workbook is open in
Excel the results are saved next to it as `<name>_results.xlsx`.

Test data placeholders understood by the framework: `<timestamp>` (unique value per run), `(blank)`, and
`<registered email>` style placeholders (the framework registers or reuses a test account).

## How a test runs

1. **Plan (once per test case).** The LLM turns the manual steps into atomic actions (click, fill, select, hover,
   scroll, upload, press, wait, observe), each with a plain-language target and a value that is always a placeholder
   for workbook data. Sensitive data is masked before it is sent. The plan is stored in SQLite and reused, so a
   repeat run spends no tokens. A fill or select value that is not in the workbook is dropped as invented data.
2. **Resolve each element** in this order:
   1. *Cache*: the stored locator must resolve to exactly one visible element that matches the stored fingerprint.
   2. *Deterministic fallback*: the other stored strategies (test id, id, name, label, role + name, href, text, CSS,
      XPath) with the same fingerprint check. Stored locators get a few seconds to appear before they count as
      missing, so animations and ajax never trigger a heal.
   3. *AI identification*: a compact list of the visible interactive elements goes to the LLM, which returns an index.
      The framework builds the locators itself and verifies them deterministically; the LLM never writes selectors.
      If the target names a literal from the test data (for example a product name), the chosen element must carry it.
   4. *Store or heal*: the element is saved with several strategies and a fingerprint. If it replaces an older
      reference, a new version is created, the step is reported as `Healed`, and the new version stays *pending* until
      a reviewer approves it (`framework.auto_approve_heals: true` approves automatically).
   5. *Refuse to guess*: below `framework.ai_confidence_threshold` the step is `Blocked` with the reason.
3. **Verify, deterministically.** Quoted messages, currency amounts, bracketed items and test-data values taken from
   the Expected Result are *binding*: each must be observed on screen (page text, button labels, browser validation
   messages, alerts) or the test is `Fail`. The LLM can only suggest where in the flow a message should appear; it
   cannot change, weaken or ignore an expectation, and a failed check is never healed. Free-prose fragments are
   reported as advisory only.

Every test case starts in a fresh, logged-out browser context.

## Configuration

* `config/applications.yaml`: one entry per application: URL per environment, login method (given to the planner),
  `registration_test_case` (replayed when a test needs a logged-in user and none exists) or a fixed `test_account`,
  `account_markers`, `planner_hints`, `block_url_patterns` (ads/trackers), `allow_test_payments`, rate limit. The
  top-level `allowed_domains` is the allow-list; the framework refuses to run against or navigate to anything else.
  `payment_guard` lists the payment-submit phrases and the only card numbers that may be used.
* `config/settings.yaml`: LLM model and pricing, confidence threshold, auto-approval of heals, headless mode, and the
  optional `verification.amount_pattern` (regular expression for money amounts; a multi-currency default is built in).

Adding another website needs a new block in `applications.yaml` and a workbook; no framework code changes.

## Logs and reports

* `logs/llm_usage.csv` and the `llm_usage` table: every LLM call with timestamp, run ID, test case, step, user, agent,
  reason (`first_learning` / `healing`), model, input/output tokens from the API usage fields, latency, status, cost.
* `logs/run_history.csv` and the `run_history` table: date, suite, pass/fail counts, tokens, cache hit rate, heals.
* `reports/<run>_report.{json,txt,md}`: totals, per-test-case results and every healed reference with old/new
  fingerprints. `logs/ai_misses.log`: what the LLM was shown when it could not identify an element.
* Credentials, card data and email addresses are masked in logs and in anything sent to the LLM, and screenshots mask
  every input field.

## Measured results (automationexercise.com, the provided 20-case suite)

| Run | Result | Tokens | Cost* | Elements served from store | Duration |
|---|---|---|---|---|---|
| 1 (learning, empty store) | 20 pass | 288,575 | $3.06 | 38% | 623 s |
| 2 | 20 pass | 0 | $0 | 100% | 207 s |
| 3 | 20 pass | 0 | $0 | 100% | 229 s |

\*estimated from the configured model price. Estimated cost per 100 steps: $2.57 on a first run, $0 on repeat runs.

Also verified: six new test cases on the same site and a five-case suite on saucedemo.com (a second application),
both passing with no code changes and replaying at 0 tokens; an expected result that is not on the page ends as `Fail`;
a deliberately broken locator was healed, flagged `Healed`, held for review and approved through the CLI.

### Second application demo

`second_application/saucedemo_test_cases.xlsx` is a twelve-case suite for https://www.saucedemo.com in the same
workbook format. The only thing the framework knows about that site is its `sauce_demo` entry in
`config/applications.yaml` (URL, login method, the public demo account, allow-list). Run it with:

```bash
python main.py second_application/saucedemo_test_cases.xlsx --application sauce_demo --environment production
```

Run it twice: the first run learns the elements, the second replays them at 0 tokens.

## Known limits

* Negative expectations ("no account is created") and per-item checks such as "every product contains X" are not
  machine-verified; the quoted messages that accompany them are.
* Elements inside iframes or shadow DOM are not offered to the LLM.
* The `Dockerfile` has not been built on the development machine (Docker was not installed there); the pip package
  was installed into a clean virtualenv and its command ran.
* When the LLM account has no credit or the key is rejected, steps that need the LLM are Blocked with that reason
  and no further calls are attempted; tests whose plan and elements are already stored still run at 0 tokens.
