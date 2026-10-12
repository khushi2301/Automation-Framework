from pathlib import Path
from datetime import datetime


class ScreenshotTool:
    def __init__(self, base_dir: str = "./screenshots"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_screenshot_path(self, run_id: str, test_case_id: str,
                           step_no: int, application: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{test_case_id}_step_{step_no}_{timestamp}.png"

        subdir = self.base_dir / application / run_id
        subdir.mkdir(parents=True, exist_ok=True)

        return str(subdir / filename)

    def cleanup_old_screenshots(self, days: int = 7) -> int:
        from datetime import timedelta
        import os

        cutoff_time = datetime.now() - timedelta(days=days)
        deleted_count = 0

        for screenshot_file in self.base_dir.rglob("*.png"):
            file_time = datetime.fromtimestamp(os.path.getmtime(screenshot_file))
            if file_time < cutoff_time:
                screenshot_file.unlink()
                deleted_count += 1

        return deleted_count
