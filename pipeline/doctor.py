"""設定健檢：逐項檢查系統可運作所需的設定，指出還缺什麼。

用法：.venv/bin/python pipeline/doctor.py
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import config_path, load_config, load_secrets, SECRETS_PATH

OK, BAD, WARN = "✅", "❌", "⚠️ "
failures = 0


def check(label: str, ok: bool, hint: str = "", warn: bool = False):
    global failures
    mark = OK if ok else (WARN if warn else BAD)
    print(f"{mark} {label}" + ("" if ok else f" — {hint}"))
    if not ok and not warn:
        failures += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate weekly-report configuration")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="skip network and paid-provider calls")
    mode.add_argument("--live", action="store_true", help="verify Worker, LINE, and LLM credentials")
    args = parser.parse_args(argv)
    live = args.live

    cfg = load_config()
    secrets = load_secrets()

    print("── 設定 ──")
    print(f"✅ config: {config_path()}")

    print("── 密鑰（~/.config/weekly-report/secrets.env）──")
    check("secrets.env 存在", SECRETS_PATH.exists(), "照 README 建立")
    tok = secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    check("LINE_CHANNEL_ACCESS_TOKEN", len(tok) > 100,
          "太短——注意是 Messaging API 頁 Issue 的 long-lived token，不是 32 位的 Channel secret")
    check("COLLECTOR_API_SECRET", len(secrets.get("COLLECTOR_API_SECRET", "")) >= 16,
          "用 openssl rand -hex 32 產生")
    check("GITHUB_TOKEN", bool(secrets.get("GITHUB_TOKEN")),
          "沒有的話自動 star 會跳過（非致命）", warn=True)

    print("\n── Collector（Cloudflare Worker）──")
    base = cfg["collector"]["base_url"]
    deployed = "YOURNAME" not in base and "workers.dev" in base
    check("config.yaml base_url 已設定", deployed, "執行 collector/deploy.sh")
    if deployed and live:
        import requests
        try:
            r = requests.get(f"{base}/links", params={"since": 0},
                             headers={"X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", "")},
                             timeout=15)
            check("Worker 可連線且密鑰正確", r.status_code == 200,
                  f"HTTP {r.status_code}（403 表示 API_SECRET 兩邊不一致）")
            if r.status_code == 200:
                n = len(r.json())
                check(f"資料庫目前有 {n} 筆連結", True)
        except Exception as e:
            check("Worker 可連線", False, str(e))

    print("\n── LINE 推送 ──")
    push_to = cfg["line"]["push_to"]
    valid_id = bool(re.match(r"^[UCR][0-9a-f]{32}$", push_to))
    check("line.push_to 格式正確", valid_id,
          f"目前值「{push_to}」不是有效的 LINE ID（應為 U/C/R 開頭 33 字元）；"
          "部署後執行 collector/get_push_id.sh 自動取得")
    if valid_id and len(tok) > 100 and live:
        import requests
        r = requests.get("https://api.line.me/v2/bot/info",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        check("LINE token 有效", r.status_code == 200, f"HTTP {r.status_code}——token 貼錯或已撤銷")

    backend = cfg["llm"].get("backend")
    print(f"\n── LLM（backend: {backend}）──")
    if backend == "api":
        check("ANTHROPIC_API_KEY", bool(secrets.get("ANTHROPIC_API_KEY")), "backend=api 需要填")
    else:
        cli = "claude" if backend == "claude-cli" else "codex"
        if shutil.which(cli) and live:
            try:
                from llm import ask
                out = ask("只回答兩個字：正常")
                check(f"{cli} 實際呼叫成功", bool(out), "有回應但為空")
            except Exception as e:
                hint = ("請在終端機跑 claude 後執行 /login" if cli == "claude"
                        else "請在終端機跑 codex 完成登入")
                check(f"{cli} 實際呼叫成功", False, f"{str(e)[:150]}——{hint}")
        elif not shutil.which(cli):
            check(f"{cli} CLI", False, f"找不到 {cli} 指令")
        else:
            check(f"{cli} CLI", True)

    print("\n── 觸發與環境 ──")
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    check("LINE 觸發輪詢已載入（com.weekly.trigger）", "com.weekly.trigger" in r.stdout,
          "執行 launchd/install.sh")
    check("ffmpeg（podcast 串接品質較佳）", bool(shutil.which("ffmpeg")),
          "沒有也能運作", warn=True)

    print()
    if failures:
        print(f"還有 {failures} 項必要設定未完成（上方 ❌ 項目）。")
        sys.exit(1)
    suffix = "（offline；再用 --live 驗證外部服務）" if not live else ""
    print(f"🎉 健檢通過{suffix}")


if __name__ == "__main__":
    main()
