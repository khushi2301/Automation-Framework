import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.models import RunSummary


def _heal_rows(heals) -> List[Dict[str, Any]]:
    rows = []
    for h in heals or []:
        rows.append({
            "locator_id": h['id'], "page": h['page'], "element": h['element_description'],
            "version": h['version'], "status": h['review_status'],
            "old": h['old'], "new": h['new'],
        })
    return rows


def _case_rows(results) -> List[Dict[str, Any]]:
    return [{
        "test_case_id": r.test_case_id, "title": r.test_case_title, "status": r.status,
        "tokens": r.total_tokens_used, "element_sources": r.element_sources,
        "actual_result": r.actual_result,
    } for r in results or []]


class ReportTool:
    """Run reports in JSON, text and Markdown. Healed references are listed individually so a reviewer can act."""

    def __init__(self, base_dir: str = "./reports"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sections(s: RunSummary):
        return {
            "test_cases": {"total": s.total_test_cases, "passed": s.passed_test_cases, "failed": s.failed_test_cases,
                           "blocked": s.blocked_test_cases, "skipped": s.skipped_test_cases},
            "steps": {"total": s.total_steps, "passed": s.passed_steps, "failed": s.failed_steps,
                      "blocked": s.blocked_steps, "skipped": s.skipped_steps},
            "llm_usage": {"total_tokens": s.total_tokens_used, "total_calls": s.total_llm_calls,
                          "cache_hits": s.cache_hit_count, "fallback_hits": s.fallback_hit_count,
                          "ai_identifications": s.ai_identification_count, "heals": s.healed_count,
                          "cache_hit_rate": f"{s.cache_hit_rate:.2%}", "false_heal_rate": f"{s.false_heal_rate:.2%}"},
            "cost": {"total_usd": round(s.total_cost_usd, 6), "per_100_steps_usd": round(s.cost_per_100_steps, 6)},
        }

    def generate_json_report(self, s: RunSummary, results=None, heals=None) -> str:
        data = {
            "run_id": s.run_id, "suite_name": s.suite_name, "application": s.application,
            "environment": s.environment,
            "execution_time": {"start": s.start_time.isoformat(), "end": s.end_time.isoformat(),
                               "total_ms": s.total_duration_ms},
            **self._sections(s),
            "test_case_results": _case_rows(results),
            "heals_awaiting_review": _heal_rows(heals),
        }
        path = self.base_dir / f"{s.run_id}_report.json"
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return str(path)

    def generate_text_report(self, s: RunSummary, results=None, heals=None) -> str:
        sec = self._sections(s)
        lines = ["=" * 80, "RUN SUMMARY REPORT", "=" * 80, "",
                 f"Run ID: {s.run_id}", f"Suite: {s.suite_name}", f"Application: {s.application}",
                 f"Environment: {s.environment}", "",
                 f"Execution Time: {s.start_time} - {s.end_time}",
                 f"Total Duration: {s.total_duration_ms / 1000:.2f} seconds", ""]
        for title, key in (("TEST CASES", "test_cases"), ("STEPS", "steps"), ("LLM USAGE", "llm_usage"), ("COST", "cost")):
            lines += [title, "-" * 40] + [f"{k.replace('_', ' ').title():<22}{v}" for k, v in sec[key].items()] + [""]
        lines += ["TEST CASE RESULTS", "-" * 40]
        for r in _case_rows(results):
            lines.append(f"{r['test_case_id']:<8}{r['status']:<9}{r['tokens']:>7} tokens  {r['title']}")
        lines += ["", "HEALED ELEMENTS (flagged for review)", "-" * 40]
        rows = _heal_rows(heals)
        if not rows:
            lines.append("None awaiting review.")
        for h in rows:
            lines += [f"{h['locator_id']}  v{h['version']}  [{h['status']}]  {h['page']}  '{h['element']}'",
                      f"    old: {h['old']}", f"    new: {h['new']}"]
        lines += ["", "=" * 80]
        path = self.base_dir / f"{s.run_id}_report.txt"
        path.write_text("\n".join(lines), encoding='utf-8')
        return str(path)

    def generate_markdown_report(self, s: RunSummary, results=None, heals=None) -> str:
        sec = self._sections(s)
        md = [f"# Run Summary Report", "", f"**Run ID:** {s.run_id}  ", f"**Suite:** {s.suite_name}  ",
              f"**Application:** {s.application}  ", f"**Environment:** {s.environment}  ", "",
              f"- **Start:** {s.start_time.isoformat(timespec='seconds')}",
              f"- **End:** {s.end_time.isoformat(timespec='seconds')}",
              f"- **Duration:** {s.total_duration_ms / 1000:.1f} s", ""]
        for title, key in (("Test cases", "test_cases"), ("Steps", "steps"), ("LLM usage", "llm_usage"), ("Cost", "cost")):
            md += [f"## {title}", "| Metric | Value |", "|---|---|"]
            md += [f"| {k.replace('_', ' ').title()} | {v} |" for k, v in sec[key].items()] + [""]
        md += ["## Test case results", "| Test case | Status | Tokens | Title |", "|---|---|---|---|"]
        md += [f"| {r['test_case_id']} | {r['status']} | {r['tokens']} | {r['title']} |" for r in _case_rows(results)]
        md += ["", "## Healed elements (flagged for review)"]
        rows = _heal_rows(heals)
        if not rows:
            md.append("None awaiting review.")
        else:
            md += ["| Locator | Version | Status | Page | Element | Old | New |", "|---|---|---|---|---|---|---|"]
            md += [f"| {h['locator_id']} | v{h['version']} | {h['status']} | {h['page']} | {h['element']} | "
                   f"{h['old']} | {h['new']} |" for h in rows]
        path = self.base_dir / f"{s.run_id}_report.md"
        path.write_text("\n".join(md), encoding='utf-8')
        return str(path)
