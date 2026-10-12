import logging
import json
import csv
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

_MASKS = [
    (re.compile(r'sk-[A-Za-z0-9_\-]{8,}'), 'sk-***'),
    (re.compile(r'(?i)\b(password|passwd|pwd|cvc|cvv|card[ _]?(?:no|number)|expiry)\b(\s*[:=]\s*)([^\s,;]+)'), r'\1\2***'),
    (re.compile(r'([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})'), r'\1***@\2'),
    (re.compile(r'\b\d{13,19}\b'), '****'),
]


def mask_sensitive(text: str) -> str:
    """Redact credentials, card numbers, API keys and email addresses before text is logged or sent to an LLM."""
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_sensitive(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        if record.exc_info:
            log_obj["exception"] = mask_sensitive(self.formatException(record.exc_info))
        return json.dumps(log_obj)


def get_logger(name: str, log_file: Optional[str] = None, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = JSONFormatter()

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger


def log_llm_usage(run_id: str, test_case_id: str, step_no: int, user_id: str,
                  agent_name: str, reason: str, model: str, input_tokens: int,
                  output_tokens: int, latency_ms: int, cost_usd: float,
                  status: str, error_message: Optional[str] = None):
    log_path = Path("./logs/llm_usage.csv")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = log_path.exists()

    with open(log_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                'timestamp', 'run_id', 'testcase_id', 'step_no', 'user_id', 'agent_name',
                'reason', 'model', 'input_tokens', 'output_tokens', 'latency_ms',
                'cost_usd', 'status', 'error_message'
            ])

        writer.writerow([
            datetime.now().isoformat(), run_id, test_case_id, step_no, user_id, agent_name,
            reason, model, input_tokens, output_tokens, latency_ms, f"{cost_usd:.6f}",
            status, mask_sensitive(error_message or "")
        ])


def log_run_history(run_id: str, suite_name: str, application: str, environment: str,
                    start_time: datetime, end_time: datetime, total_duration_ms: float,
                    test_stats: dict, step_stats: dict, token_stats: dict, cost_stats: dict):
    log_path = Path("./logs/run_history.csv")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = log_path.exists()

    with open(log_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                'timestamp', 'run_id', 'suite_name', 'application', 'environment',
                'start_time', 'end_time', 'total_duration_ms',
                'total_test_cases', 'passed_test_cases', 'failed_test_cases',
                'blocked_test_cases', 'skipped_test_cases',
                'total_steps', 'passed_steps', 'failed_steps', 'blocked_steps', 'skipped_steps',
                'total_tokens_used', 'llm_calls', 'cache_hits', 'cache_hit_rate', 'heals',
                'total_cost_usd', 'cost_per_100_steps'
            ])

        writer.writerow([
            datetime.now().isoformat(), run_id, suite_name, application, environment,
            start_time.isoformat(), end_time.isoformat(), f"{total_duration_ms:.2f}",
            test_stats.get('total', 0), test_stats.get('passed', 0), test_stats.get('failed', 0),
            test_stats.get('blocked', 0), test_stats.get('skipped', 0),
            step_stats.get('total', 0), step_stats.get('passed', 0), step_stats.get('failed', 0),
            step_stats.get('blocked', 0), step_stats.get('skipped', 0),
            token_stats.get('total_tokens', 0), token_stats.get('llm_calls', 0),
            token_stats.get('cache_hits', 0), f"{token_stats.get('cache_hit_rate', 0.0):.4f}",
            token_stats.get('heals', 0),
            f"{cost_stats.get('total', 0):.6f}", f"{cost_stats.get('per_100_steps', 0.0):.6f}"
        ])
