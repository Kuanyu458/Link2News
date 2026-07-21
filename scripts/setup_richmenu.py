"""LINE Rich Menu 一鍵設定：渲染選單圖 → 建立選單 → 上傳圖片 → 設為預設。

用法：.venv/bin/python scripts/setup_richmenu.py
重跑安全：會先刪除本系統建立的舊選單（名稱 weekly-report-menu）再重建。
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from common import load_secrets  # noqa: E402

MENU_NAME = "weekly-report-menu"
API = "https://api.line.me/v2/bot"
API_DATA = "https://api-data.line.me/v2/bot"

BUTTONS = ["生成週報", "重新生成上次週報", "指定生成模型", "查看待處理清單", "本週新術語介紹"]


def render_image(png_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    html = (ROOT / "scripts" / "richmenu.html").resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 2500, "height": 843})
        page.goto(html)
        page.wait_for_timeout(500)
        page.screenshot(path=str(png_path))
        browser.close()
    print(f"✔ 選單圖已渲染：{png_path}（{png_path.stat().st_size // 1024} KB）")


def main():
    secrets = load_secrets()
    token = secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        sys.exit("❌ secrets.env 缺 LINE_CHANNEL_ACCESS_TOKEN")
    auth = {"Authorization": f"Bearer {token}"}

    png = ROOT / "scripts" / "richmenu.png"
    render_image(png)

    # 先保留舊選單；新版建立、上傳並設為預設成功後才移除，避免部署中斷時沒有選單。
    r = requests.get(f"{API}/richmenu/list", headers=auth, timeout=30)
    r.raise_for_status()
    old_menu_ids = [m["richMenuId"] for m in r.json().get("richmenus", [])
                    if m.get("name") == MENU_NAME]

    # 建立選單（2500x843，五等分，點擊即送出對應文字指令）
    cell_w = 2500 // len(BUTTONS)
    body = {
        "size": {"width": 2500, "height": 843},
        "selected": True,
        "name": MENU_NAME,
        "chatBarText": "週報選單",
        "areas": [
            {"bounds": {"x": i * cell_w, "y": 0,
                        "width": 2500 - i * cell_w if i == len(BUTTONS) - 1 else cell_w,
                        "height": 843},
             "action": {"type": "message", "text": label}}
            for i, label in enumerate(BUTTONS)
        ],
    }
    r = requests.post(f"{API}/richmenu", headers={**auth, "Content-Type": "application/json"},
                      json=body, timeout=30)
    r.raise_for_status()
    menu_id = r.json()["richMenuId"]
    print(f"✔ 選單已建立：{menu_id}")

    # 上傳圖片
    r = requests.post(f"{API_DATA}/richmenu/{menu_id}/content",
                      headers={**auth, "Content-Type": "image/png"},
                      data=png.read_bytes(), timeout=60)
    r.raise_for_status()
    print("✔ 選單圖已上傳")

    # 設為所有使用者的預設選單
    r = requests.post(f"{API}/user/all/richmenu/{menu_id}", headers=auth, timeout=30)
    r.raise_for_status()
    print("✔ 已設為預設選單")

    for old_id in old_menu_ids:
        if old_id == menu_id:
            continue
        r = requests.delete(f"{API}/richmenu/{old_id}", headers=auth, timeout=30)
        r.raise_for_status()
        print(f"✔ 已刪除舊選單 {old_id}")
    print("\n🎉 完成！打開與機器人的 LINE 聊天室，下方會出現「週報選單」五鍵選單")
    print("   （若沒立即出現，關掉聊天室重開即可）")


if __name__ == "__main__":
    main()
