"""LaunchAgent entry point: lock locally, claim one cloud job, then run it."""
import fcntl
import os
import sys
from pathlib import Path

import requests

PIPELINE_DIR = Path(__file__).resolve().parent
ROOT = PIPELINE_DIR.parent
sys.path.insert(0, str(PIPELINE_DIR))

from common import line_push, load_config, load_secrets  # noqa: E402
import main as pipeline_main  # noqa: E402


def run() -> int:
    (ROOT / "output").mkdir(parents=True, exist_ok=True)
    lock_file = (ROOT / "output" / ".pipeline.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    cfg = load_config()
    secrets = load_secrets()
    base = cfg["collector"]["base_url"].rstrip("/")
    response = requests.post(
        f"{base}/api/v1/jobs/claim",
        headers={"Authorization": f"Bearer {secrets.get('COLLECTOR_API_SECRET', '')}"},
        timeout=20,
    )
    if response.status_code == 204:
        return 0
    response.raise_for_status()
    job = response.json()["job"]

    mode = job.get("mode") or "all"
    week = job.get("week") or ""
    model = job.get("model") or ""
    target = cfg.get("line", {}).get("push_to", "")
    if mode == "regenerate" and week:
        line_push(secrets, target, f"🔁 開始重新生成 {week}，完成後會通知你。")
        args = ["--regenerate", "--week", week]
    else:
        line_push(secrets, target, "🗞️ 開始製作本週週報，完成後會通知你。")
        args = ["--all"]
    if model:
        args.extend(["--llm", model])
    args.extend(["--job-id", job["id"]])

    os.environ["WEEKLY_REPORT_LOCK_HELD"] = "1"
    sys.argv = [str(PIPELINE_DIR / "main.py"), *args]
    # Keep lock_file referenced for the complete pipeline run.
    pipeline_main.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
