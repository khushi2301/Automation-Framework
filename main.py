#!/usr/bin/env python3
import asyncio
import argparse
import json
import yaml
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

from src.framework import ExecutionWorkflow
from src.storage import DatabaseManager
from src.tools import LocatorStoreTool
from src.utils import get_logger


logger = get_logger(__name__, log_file="./logs/main.log")


def load_config(config_file: str = "./config/settings.yaml") -> dict:
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def resolve_config(explicit: str | None, name: str) -> str:
    """An explicit path wins, then ./config/<name>, then the copy shipped inside the installed package."""
    if explicit:
        return explicit
    local = Path('config') / name
    if local.exists():
        return str(local)
    bundled = Path(__file__).parent / 'src' / 'defaults' / name
    if bundled.exists():
        logger.info(f"No ./config/{name} found: using the default shipped with the package")
        return str(bundled)
    return str(local)  # missing: let the open() fail with a clear path


def ods_to_xlsx(path: str) -> str:
    """An OpenDocument workbook is converted to an .xlsx next to it, which is what the framework reads and writes
    results into. An existing .xlsx that is newer than the .ods is reused so earlier results are not overwritten."""
    src = Path(path)
    if src.suffix.lower() != '.ods':
        return path
    out = src.with_name(f"{src.stem}_converted.xlsx")  # never overwrite a user's own .xlsx of the same name
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        logger.info(f"Using {out.name} (newer than {src.name}); results are written there")
        return str(out)
    import pandas as pd  # only needed for .ods input
    sheets = pd.read_excel(src, engine='odf', sheet_name=None, dtype=str)
    with pd.ExcelWriter(out) as writer:
        for name, df in sheets.items():
            df = df.loc[:, ~df.columns.astype(str).str.startswith('Unnamed')]
            df.to_excel(writer, sheet_name=name, index=False)
    logger.info(f"Converted {src.name} to {out.name}; results are written to the .xlsx, not the .ods")
    return str(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI-Powered Self-Healing Test Automation Framework")
    p.add_argument('excel_file', nargs='?', help='Path to the Excel test suite (rows marked Execute=Y are run)')
    p.add_argument('--application', help='Application name from config/applications.yaml')
    p.add_argument('--environment', help='Environment name (e.g. production)')
    p.add_argument('--suite-name', default='Test Suite', help='Name of the test suite')
    p.add_argument('--config', help='Path to settings.yaml (default: ./config/settings.yaml, else the packaged default)')
    p.add_argument('--applications-config',
                   help='Path to applications.yaml: applications, environments, allow-list '
                        '(default: ./config/applications.yaml, else the packaged default)')
    p.add_argument('--only', help='Comma separated test case IDs; run just these (they must still be marked Y)')
    p.add_argument('--replan', action='store_true',
                   help='Discard stored plans for the selected test cases and plan them again (uses tokens)')
    p.add_argument('--slow-mo', type=int, default=0, metavar='MS',
                   help='Demo aid: wait this many milliseconds between browser actions, e.g. 700')
    p.add_argument('--hold', type=float, default=0, metavar='SECONDS',
                   help='Demo aid: keep the final page of each test open this long, e.g. 3')
    p.add_argument('--headless', action='store_true', default=False, help='Run browser in headless mode')
    p.add_argument('--no-headless', action='store_false', dest='headless', help='Run browser visibly (default)')

    admin = p.add_argument_group('element reference store')
    admin.add_argument('--list-locators', action='store_true', help='List active stored references')
    admin.add_argument('--export-locators', metavar='FILE', help='Export all references (with history state) to JSON')
    admin.add_argument('--import-locators', metavar='FILE', help='Import references from a JSON export')
    admin.add_argument('--invalidate-locator', metavar='ID', help='Invalidate one reference')
    admin.add_argument('--invalidate-page', metavar='PAGE', help="Invalidate every reference on a page, e.g. /login")
    admin.add_argument('--invalidate-all', action='store_true', help='Invalidate every reference of the application')
    admin.add_argument('--list-heals', action='store_true', help='List healed references awaiting review')
    admin.add_argument('--approve-heal', metavar='ID', help='Approve a healed reference so it replaces the old one')
    admin.add_argument('--reject-heal', metavar='ID', help='Reject a healed reference; the old one stays')
    admin.add_argument('--reviewer', default='reviewer', help='Reviewer name recorded with a decision')
    return p


def run_admin(args, config) -> bool:
    """Handle reference-store commands. Returns True if one was handled."""
    wanted = any([args.list_locators, args.export_locators, args.import_locators, args.invalidate_locator,
                  args.invalidate_page, args.invalidate_all, args.list_heals, args.approve_heal, args.reject_heal])
    if not wanted:
        return False

    store = LocatorStoreTool(DatabaseManager(config.get('database', {}).get('path', './data/framework.db')))
    app, env = args.application, args.environment

    if args.import_locators:
        data = json.loads(Path(args.import_locators).read_text(encoding='utf-8'))
        print(f"Imported {store.import_locators(data)} references")
    if args.approve_heal:
        print("Approved" if store.approve_heal(args.approve_heal, args.reviewer) else "No pending heal with that ID")
    if args.reject_heal:
        print("Rejected" if store.reject_heal(args.reject_heal, args.reviewer) else "No pending heal with that ID")
    if args.invalidate_locator:
        print("Invalidated" if store.invalidate_locator(args.invalidate_locator) else "No reference with that ID")
    if (args.invalidate_page or args.invalidate_all or args.list_locators or args.export_locators) and not (app and env):
        print("--application and --environment are required for this command")
        return True
    if args.invalidate_page:
        print(f"Invalidated {store.invalidate_page(app, env, args.invalidate_page)} references on {args.invalidate_page}")
    if args.invalidate_all:
        print(f"Invalidated {store.invalidate_all(app, env)} references")
    if args.list_heals:
        pending = store.list_pending_heals(app, env)
        print(f"{len(pending)} healed reference(s) awaiting review")
        for l in pending:
            old = store.db.get_locator(l.replaces_id) if l.replaces_id else None
            print(f"  {l.id}  v{l.version}  page={l.page}  element='{l.element_description}'")
            if old:
                print(f"      old: <{old.fingerprint.tag}> text={old.fingerprint.text!r} via {old.locator_strategy}")
            print(f"      new: <{l.fingerprint.tag}> text={l.fingerprint.text!r} via {l.locator_strategy}")
    if args.list_locators:
        for l in store.list_locators(app, env):
            print(f"  {l.id}  v{l.version}  {l.review_status:8}  {l.page}  '{l.element_description}'  "
                  f"-> {l.locator_strategy}: {l.primary_locator}")
    if args.export_locators:
        Path(args.export_locators).write_text(json.dumps(store.export_locators(app, env), indent=2), encoding='utf-8')
        print(f"Exported to {args.export_locators}")
    return True


def main():
    args = build_parser().parse_args()

    load_dotenv(find_dotenv(usecwd=True))  # .env in the working directory or any parent (works when pip-installed)
    load_dotenv(Path(__file__).parent.parent / '.env')

    try:
        args.config = resolve_config(args.config, 'settings.yaml')
        args.applications_config = resolve_config(args.applications_config, 'applications.yaml')
        config = load_config(args.config)
        config.setdefault('framework', {})['headless_mode'] = args.headless
        config['applications_config'] = args.applications_config
        config['framework']['slow_mo'] = args.slow_mo
        config['framework']['hold_seconds'] = args.hold

        if run_admin(args, config):
            return 0

        if not (args.excel_file and args.application and args.environment):
            print("A run needs: excel_file --application NAME --environment NAME")
            return 2
        if not Path(args.excel_file).exists():
            logger.error(f"Excel file not found: {args.excel_file}")
            return 1
        args.excel_file = ods_to_xlsx(args.excel_file)

        only = [t.strip() for t in args.only.split(',')] if args.only else None
        logger.info(f"Starting {args.suite_name}: {args.application}/{args.environment}, file={args.excel_file}")

        workflow = ExecutionWorkflow(config)
        s = asyncio.run(workflow.execute_test_suite(
            args.excel_file, args.application, args.environment, args.suite_name,
            only=only, replan=args.replan))

        print("\n" + "=" * 80)
        print("TEST EXECUTION SUMMARY")
        print("=" * 80)
        print(f"Run ID: {s.run_id}   Suite: {s.suite_name}   {s.application}/{s.environment}")
        print(f"Duration: {s.total_duration_ms / 1000:.1f}s")
        print(f"Test cases: {s.passed_test_cases} passed, {s.failed_test_cases} failed, "
              f"{s.blocked_test_cases} blocked, {s.skipped_test_cases} skipped")
        print(f"Steps: {s.passed_steps} passed, {s.failed_steps} failed, {s.blocked_steps} blocked")
        print(f"LLM: {s.total_tokens_used} tokens in {s.total_llm_calls} calls, ${s.total_cost_usd:.4f} "
              f"(${s.cost_per_100_steps:.4f} per 100 steps)")
        print(f"Element lookups served from the store: {s.cache_hit_rate:.1%} "
              f"(cache {s.cache_hit_count}, fallback {s.fallback_hit_count}, AI {s.ai_identification_count}, "
              f"healed {s.healed_count})")
        print("=" * 80)
        for r in workflow.execution_results:
            if r.status != "Skipped":
                print(f"  {r.test_case_id:7} {r.status:8} {r.total_tokens_used:6} tokens  {r.actual_result[:110]}")
        return 0

    except Exception as e:
        logger.error(f"Test execution failed: {str(e)}", exc_info=True)
        return 1


if __name__ == '__main__':
    exit(main())
