"""共用工具：設定載入、週期計算、LINE 推送、logging。"""
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_CONFIG_DIR = Path.home() / ".config" / "weekly-report"
SECRETS_PATH = USER_CONFIG_DIR / "secrets.env"
DEFAULT_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"
LEGACY_CONFIG_PATH = PROJECT_ROOT / "pipeline" / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "pipeline" / "config.example.yaml"

log = logging.getLogger("weekly")


def setup_logging(week_key: str) -> None:
    log_dir = PROJECT_ROOT / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / f"{week_key}.log", encoding="utf-8"),
        ],
    )


def config_path() -> Path:
    configured = os.environ.get("WEEKLY_REPORT_CONFIG", "").strip()
    candidates = [Path(configured).expanduser()] if configured else [DEFAULT_CONFIG_PATH]
    # Backward compatibility for existing private installations. Public clones
    # do not track this path; new installations use DEFAULT_CONFIG_PATH.
    candidates.append(LEGACY_CONFIG_PATH)
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"Missing config.yaml. Copy {EXAMPLE_CONFIG_PATH} to {DEFAULT_CONFIG_PATH}."
        )
    return path


def load_config() -> dict:
    path = config_path()
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_secrets() -> dict:
    """讀 KEY=VALUE 格式的 secrets.env，並疊加到環境變數之上。"""
    secrets = {}
    if SECRETS_PATH.exists():
        for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            secrets[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        secrets.setdefault(k, v)
    return secrets


def week_key(date: dt.date | None = None) -> str:
    """本週的 ISO 週鍵，如 2026-W28。"""
    d = date or dt.date.today()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_start_ms(date: dt.date | None = None) -> int:
    """本週一 00:00 的 epoch 毫秒（LINE timestamp 同單位）。"""
    d = date or dt.date.today()
    monday = d - dt.timedelta(days=d.weekday())
    return int(dt.datetime.combine(monday, dt.time.min).timestamp() * 1000)


def output_dir(cfg: dict, wk: str) -> Path:
    out = PROJECT_ROOT / cfg.get("output_dir", "output") / wk
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------- LINE push ----------

def line_push_messages(secrets: dict, to: str, messages: list[dict]) -> bool:
    """推送一組 LINE message；Messaging API 單次最多五則。"""
    token = secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token or not to:
        log.warning("LINE push skipped (missing token or recipient)")
        return False
    if not messages:
        return True
    for start in range(0, len(messages), 5):
        batch = messages[start:start + 5]
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {token}"},
            json={"to": to, "messages": batch},
            timeout=30,
        )
        if r.status_code != 200:
            log.error("LINE push failed %s: %s", r.status_code, r.text)
            return False
    return True


def line_push(secrets: dict, to: str, text: str) -> bool:
    """LINE 文字推送。單則上限 5000 字，自動截斷分段。"""
    chunks = [text[i:i + 4900] for i in range(0, len(text), 4900)] or [text]
    return line_push_messages(
        secrets, to, [{"type": "text", "text": chunk} for chunk in chunks[:5]])


# ---------- Collector API ----------

def collector_get(cfg: dict, secrets: dict, path: str, params: dict) -> list | dict:
    base = cfg["collector"]["base_url"].rstrip("/")
    r = requests.get(
        f"{base}{path}",
        params=params,
        headers={"X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", "")},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def collector_await_selection(cfg: dict, secrets: dict, wk: str | None) -> None:
    base = cfg["collector"]["base_url"].rstrip("/")
    requests.post(
        f"{base}/await-selection",
        json={"week": wk or ""},
        headers={"X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", "")},
        timeout=30,
    ).raise_for_status()


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


URL_RE = re.compile(r"https?://[^\s\"'<>）)\]】」]+")
