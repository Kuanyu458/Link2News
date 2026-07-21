"""統一 LLM 呼叫層。

backend = "claude-cli"：headless Claude Code（`claude -p`），用 Claude 訂閱。
backend = "codex-cli" ：headless Codex CLI（`codex exec`），用 ChatGPT/Codex 訂閱。
backend = "api"       ：Anthropic Messages API（需 ANTHROPIC_API_KEY）。

在 ~/.config/weekly-report/config.yaml 的 llm.backend 切換。
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile

from common import load_config, load_secrets

log = logging.getLogger("weekly.llm")
_cfg = None


def _config():
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def _clean_env() -> dict:
    """剔除巢狀 agent session 的環境變數，避免 CLI 驗證失敗。"""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("CLAUDE_CODE", "ANTHROPIC_BASE_URL", "CLAUDECODE",
                                 "OPENAI_BASE_URL", "CODEX_"))}


def ask(prompt: str, system: str = "", max_tokens: int = 8000,
        allow_web_search: bool = False, timeout: int = 1200) -> str:
    cfg = _config()["llm"]
    backend = cfg.get("backend", "claude-cli")
    if backend == "api":
        return _ask_api(prompt, system, max_tokens, cfg, allow_web_search, timeout)
    if backend == "codex-cli":
        return _ask_codex(prompt, system, cfg, allow_web_search, timeout)
    return _ask_claude(prompt, system, cfg, allow_web_search, timeout)


def _run_with_retry(cmd: list[str], stdin_text: str | None, read_result,
                    timeout: int = 1200) -> str:
    last_err = ""
    for attempt in range(2):
        try:
            r = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                               timeout=timeout, env=_clean_env())
        except subprocess.TimeoutExpired:
            last_err = f"timed out after {timeout} seconds"
            log.warning("%s attempt %d failed: %s", cmd[0], attempt + 1, last_err)
            continue
        if r.returncode == 0:
            out = read_result(r)
            if out:
                return out
            last_err = "empty output"
        else:
            last_err = (r.stderr or r.stdout)[:500]
        log.warning("%s attempt %d failed (rc=%d): %s", cmd[0], attempt + 1,
                    r.returncode, last_err)
    raise RuntimeError(f"{cmd[0]} failed: {last_err}")


def _ask_claude(prompt: str, system: str, cfg: dict, allow_web_search: bool,
                timeout: int) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("找不到 claude CLI；請安裝 Claude Code 或切換 llm.backend")
    cmd = [claude, "-p", "--output-format", "text"]
    if model := (cfg.get("claude_model") or cfg.get("model")):
        cmd += ["--model", model]
    if allow_web_search:
        cmd += ["--allowedTools", "WebSearch,WebFetch"]
    if system:
        cmd += ["--append-system-prompt", system]
    return _run_with_retry(cmd, prompt, lambda r: r.stdout.strip(), timeout)


def _ask_codex(prompt: str, system: str, cfg: dict, allow_web_search: bool,
               timeout: int) -> str:
    configured = cfg.get("codex_path", "")
    if configured and "/" in configured:
        codex = os.path.expanduser(configured)
    else:
        codex = shutil.which(configured or "codex")
    if not codex or not os.path.isfile(codex) or not os.access(codex, os.X_OK):
        raise RuntimeError(
            f"找不到可執行的 codex CLI：{codex or configured or '未安裝'}")
    full_prompt = (system + "\n\n" + prompt) if system else prompt
    outfile = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    outfile.close()
    cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
           "--output-last-message", outfile.name]
    if cfg.get("codex_model"):
        cmd += ["--model", cfg["codex_model"]]
    if allow_web_search and cfg.get("codex_search", False):
        cmd += ["--search"]  # 舊版 codex 不支援此旗標，故需在 config 明確開啟
    cmd += ["-"]  # prompt 從 stdin 讀
    try:
        def read_result(r):
            with open(outfile.name, encoding="utf-8") as f:
                return f.read().strip()
        return _run_with_retry(cmd, full_prompt, read_result, timeout)
    finally:
        os.unlink(outfile.name)


def _ask_api(prompt: str, system: str, max_tokens: int, cfg: dict,
             allow_web_search: bool, timeout: int) -> str:
    import requests
    secrets = load_secrets()
    model = cfg.get("claude_model") or cfg.get("model")
    if not model:
        raise RuntimeError("llm.backend=api requires llm.claude_model")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    if allow_web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": secrets.get("ANTHROPIC_API_KEY", ""),
                 "anthropic-version": "2023-06-01"},
        json=body, timeout=min(timeout, 600),
    )
    r.raise_for_status()
    parts = r.json()["content"]
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def ask_json(prompt: str, system: str = "", max_tokens: int = 8000) -> dict | list:
    """要求模型輸出 JSON 並解析（容忍 ```json 圍欄）。"""
    out = ask(prompt + "\n\n只輸出合法 JSON，不要任何其他文字。", system, max_tokens)
    text = out.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
