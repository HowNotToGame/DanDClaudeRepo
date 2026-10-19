"""DM engines: who plays the Dungeon Master behind the web client.

* claude — Claude Code, headless (`claude -p`), with RoboDM's skills, hooks and subagents. Model and effort are
  passed through (`--model`, `--effort`).
* ollama — a local model served by Ollama (e.g. `ollama pull llama3.1:8b`), via its /api/chat with tool calling.
* kobold — KoboldCpp, via its OpenAI-compatible /v1/chat/completions.
* openai — any OpenAI-compatible server (LM Studio, llama.cpp server, vLLM…).

With a local engine RoboDM runs the DM loop itself (robodm/engines/local.py): the same ./dm tools, the code gate,
receipts, transcript and a local rules-lawyer pass. The choice is per machine, not per campaign: it lives in
<home>/.cache/engine.json (gitignored), set from the web client's ⚙ panel.
"""

from __future__ import annotations

import copy
import json
import time
import urllib.error
import urllib.request

from robodm import paths
from robodm.util import RoboDMError, read_json, write_json

ENGINES = {"claude": "Claude Code", "ollama": "Ollama", "kobold": "KoboldCpp", "openai": "OpenAI-compatible server"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_MODELS = ("", "fable", "opus", "sonnet", "haiku")
TOOL_MODES = ("native", "text")
DEFAULTS = {
    "engine": "claude",
    "effort": "",
    "claude": {"model": ""},
    "ollama": {"url": "http://127.0.0.1:11434", "model": "llama3.1:8b", "num_ctx": 12288, "tool_mode": "native"},
    "kobold": {"url": "http://127.0.0.1:5001", "model": "", "num_ctx": 8192, "tool_mode": "native"},
    "openai": {"url": "http://127.0.0.1:8000/v1", "model": "", "api_key": "", "num_ctx": 8192, "tool_mode": "native"},
}
# How hard a local engine works per turn.
EFFORT_PROFILES = {
    "low": {"rounds": 4, "lawyer": "judgement", "temperature": 0.8, "review": False},
    "medium": {"rounds": 8, "lawyer": "judgement", "temperature": 0.7, "review": False},
    "high": {"rounds": 12, "lawyer": "all", "temperature": 0.6, "review": False},
    "xhigh": {"rounds": 16, "lawyer": "all", "temperature": 0.5, "review": True},
    "max": {"rounds": 24, "lawyer": "all", "temperature": 0.4, "review": True},
}


def config_path():
    return paths.home() / ".cache" / "engine.json"


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    saved = read_json(config_path()) or {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def update(patch: dict) -> dict:
    """Validate and save a partial change (from the web client's settings panel)."""
    cfg = load()
    if "engine" in patch:
        if patch["engine"] not in ENGINES:
            raise RoboDMError(f"engine must be one of: {', '.join(ENGINES)}")
        cfg["engine"] = patch["engine"]
    if "effort" in patch:
        if patch["effort"] not in ("",) + EFFORTS:
            raise RoboDMError(f"effort must be one of: {', '.join(EFFORTS)}")
        cfg["effort"] = patch["effort"]
    for name in ENGINES:
        sub = patch.get(name)
        if not isinstance(sub, dict):
            continue
        for k, v in sub.items():
            if k not in DEFAULTS[name]:
                raise RoboDMError(f"Unknown {name} setting '{k}'.")
            if k == "num_ctx":
                v = int(v)
                if not 2048 <= v <= 262144:
                    raise RoboDMError("num_ctx (context window) must be between 2048 and 262144 tokens.")
            if k == "tool_mode" and v not in TOOL_MODES:
                raise RoboDMError(f"tool_mode must be one of: {', '.join(TOOL_MODES)}")
            if k == "url":
                v = str(v).strip().rstrip("/")
                if not v.startswith(("http://", "https://")):
                    raise RoboDMError("The server URL must start with http:// or https://")
            if k == "model" and name == "claude" and v and v not in CLAUDE_MODELS and not str(v).startswith("claude-"):
                raise RoboDMError("Claude model: fable, opus, sonnet, haiku, a full model id (claude-…), or default.")
            if k == "api_key" and v == "••••":
                continue                          # the masked value came back unchanged
            cfg[name][k] = v
    write_json(config_path(), cfg)
    return cfg


def public(cfg: dict) -> dict:
    """The config for the web page (API keys masked)."""
    out = copy.deepcopy(cfg)
    if out["openai"].get("api_key"):
        out["openai"]["api_key"] = "••••"
    return out


def effort_profile(cfg: dict) -> dict:
    return EFFORT_PROFILES.get(cfg.get("effort") or "medium", EFFORT_PROFILES["medium"])


def is_local(cfg: dict) -> bool:
    return cfg.get("engine", "claude") != "claude"


def base_url(cfg: dict, engine: str | None = None) -> str:
    engine = engine or cfg["engine"]
    url = cfg[engine]["url"].rstrip("/")
    if engine == "kobold" and not url.endswith("/v1"):
        url += "/v1"
    return url


# =============================================================================== probing servers

def _get(url: str, timeout: float = 4, key: str = "") -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 — the host chose this URL
        return json.loads(r.read().decode("utf-8") or "{}")


_REACH: dict[str, tuple[float, bool, str]] = {}


def reachable(cfg: dict, engine: str | None = None, fresh: bool = False) -> tuple[bool, str]:
    """Is the local server up? Cached for 10 seconds."""
    engine = engine or cfg["engine"]
    if engine == "claude":
        return True, ""
    key = f"{engine}:{cfg[engine]['url']}"
    hit = _REACH.get(key)
    if hit and not fresh and time.time() - hit[0] < 10:
        return hit[1], hit[2]
    try:
        models = list_models(cfg, engine)
        ok, detail = True, f"{len(models)} model(s)"
    except Exception as e:  # connection refused, timeout, bad JSON…
        ok, detail = False, str(getattr(e, "reason", e))
    _REACH[key] = (time.time(), ok, detail)
    return ok, detail


def list_models(cfg: dict, engine: str | None = None) -> list[str]:
    engine = engine or cfg["engine"]
    if engine == "claude":
        return [m for m in CLAUDE_MODELS if m]
    if engine == "ollama":
        data = _get(cfg["ollama"]["url"].rstrip("/") + "/api/tags")
        return sorted(m.get("name") or m.get("model") for m in data.get("models", []))
    data = _get(base_url(cfg, engine) + "/models", key=cfg[engine].get("api_key", ""))
    names = [m.get("id") for m in data.get("data", []) if m.get("id")]
    if engine == "kobold" and not names:
        try:
            names = [_get(cfg["kobold"]["url"].rstrip("/") + "/api/v1/model").get("result", "")]
        except Exception:
            pass
    return [n for n in names if n]


def help_text(cfg: dict) -> str:
    e = cfg["engine"]
    if e == "ollama":
        return (f"Ollama isn't answering at {cfg['ollama']['url']}. Start it (the Ollama app, or `ollama serve`) and "
                f"pull the model: `ollama pull {cfg['ollama']['model'] or 'llama3.1:8b'}`.")
    if e == "kobold":
        return (f"KoboldCpp isn't answering at {cfg['kobold']['url']}. Start koboldcpp with a model "
                "(e.g. a Llama 3.1 8B Instruct GGUF) and keep its default port 5001, or set the URL in ⚙.")
    if e == "openai":
        return f"No OpenAI-compatible server at {cfg['openai']['url']}. Start it, or set the URL in ⚙."
    return ""
