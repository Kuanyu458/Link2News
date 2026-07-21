"""自動 star 本週分享的 GitHub repo。"""
import logging

import requests

log = logging.getLogger("weekly.star")


def star_repos(repos: list[dict], secrets: dict) -> list[str]:
    """回傳成功 star 的 repo 全名清單。"""
    token = secrets.get("GITHUB_TOKEN", "")
    if not token:
        log.warning("GITHUB_TOKEN not set; skipping auto-star")
        return []
    starred = []
    for r in repos:
        full = f"{r['owner']}/{r['repo']}"
        try:
            resp = requests.put(
                f"https://api.github.com/user/starred/{full}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                timeout=30,
            )
            if resp.status_code == 204:
                starred.append(full)
                log.info("starred %s", full)
            else:
                log.warning("star failed %s: %s", full, resp.status_code)
        except requests.RequestException as e:
            log.warning("star error %s: %s", full, e)
    return starred
