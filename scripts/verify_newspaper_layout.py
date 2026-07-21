"""擷取週報桌面/手機畫面，並檢查橫向溢出與主要可讀性指標。"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


VIEWPORTS = {
    "desktop": {"width": 1200, "height": 900},
    "mobile": {"width": 390, "height": 844},
}


def inspect_layout(page) -> dict:
    return page.evaluate("""
        () => {
          const root = document.documentElement;
          const overflowing = [...document.querySelectorAll('body *')]
            .filter((el) => {
              const rect = el.getBoundingClientRect();
              return rect.right > root.clientWidth + 1 || rect.left < -1;
            })
            .slice(0, 12)
            .map((el) => ({
              tag: el.tagName.toLowerCase(),
              className: String(el.className || ''),
              left: Math.round(el.getBoundingClientRect().left),
              right: Math.round(el.getBoundingClientRect().right),
            }));
          const firstBody = document.querySelector('.body-cols');
          const firstHeadline = document.querySelector('.headline');
          return {
            viewportWidth: root.clientWidth,
            scrollWidth: root.scrollWidth,
            horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
            overflowing,
            bodyFontSize: firstBody ? getComputedStyle(firstBody).fontSize : null,
            headlineFontSize: firstHeadline ? getComputedStyle(firstHeadline).fontSize : null,
            title: document.title,
          };
        }
    """)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, viewport in VIEWPORTS.items():
            page = browser.new_page(viewport=viewport, device_scale_factor=1)
            page.emulate_media(media="screen")
            page.goto(args.html.resolve().as_uri(), wait_until="networkidle")
            page.screenshot(
                path=str(args.output_dir / f"{name}.png"),
                full_page=True,
            )
            results[name] = inspect_layout(page)
            page.close()
        browser.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(result["horizontalOverflow"] for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
