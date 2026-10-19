"""Talking to local model servers: Ollama's /api/chat and the OpenAI-compatible /chat/completions (KoboldCpp,
LM Studio, llama.cpp server, vLLM…). Standard library only; responses are streamed so the page can show text as
it arrives.

Messages use one neutral shape and are converted per server:
  {"role": "system"|"user"|"assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from robodm.util import RoboDMError


class ToolsUnsupported(RoboDMError):
    """The server or model rejected tool definitions (fall back to the text protocol)."""


def _ollama_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", "dm")})
        elif m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or "",
                        "tool_calls": [{"function": {"name": t["name"], "arguments": t["arguments"]}} for t in m["tool_calls"]]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m["content"]})
        elif m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": t["id"], "type": "function",
                                        "function": {"name": t["name"], "arguments": json.dumps(t["arguments"])}}
                                       for t in m["tool_calls"]]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _post(url: str, body: dict, key: str = "", timeout: float = 600):
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)                # noqa: S310 — the host chose this URL
    except urllib.error.HTTPError as e:
        with e:
            detail = e.read().decode("utf-8", "replace")[:400]
        if e.code in (400, 422, 500) and "tool" in detail.lower():
            raise ToolsUnsupported(f"the server rejected tool calling: {detail}")
        raise RoboDMError(f"{url} answered HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RoboDMError(f"can't reach {url}: {e.reason}")


def chat(cfg: dict, engine: str, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
         on_delta=None, temperature: float = 0.7, max_tokens: int | None = None,
         cancel: threading.Event | None = None) -> dict:
    """One model call. Returns {"content", "tool_calls": [{"id","name","arguments"}], "tokens": int}."""
    from robodm.engines import base_url

    sub = cfg[engine]
    if engine == "ollama":
        body = {"model": sub["model"], "messages": _ollama_messages(messages), "stream": True, "keep_alive": "30m",
                "options": {"num_ctx": int(sub.get("num_ctx") or 8192), "temperature": temperature}}
        if max_tokens:
            body["options"]["num_predict"] = max_tokens
        if tools:
            body["tools"] = tools
        if json_mode:
            body["format"] = "json"
        return _read_ollama(_post(sub["url"].rstrip("/") + "/api/chat", body), on_delta, cancel)
    body = {"model": sub.get("model") or "local", "messages": _openai_messages(messages), "stream": True,
            "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = base_url(cfg, engine) + "/chat/completions"
    try:
        resp = _post(url, body, sub.get("api_key", ""))
    except RoboDMError:
        if not json_mode:
            raise
        body.pop("response_format", None)                 # not every server knows response_format
        resp = _post(url, body, sub.get("api_key", ""))
    return _read_openai(resp, on_delta, cancel)


def _read_ollama(resp, on_delta, cancel) -> dict:
    content, calls, tokens = [], [], 0
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                if "tool" in str(ev["error"]).lower():
                    raise ToolsUnsupported(str(ev["error"]))
                raise RoboDMError(f"Ollama: {ev['error']}")
            msg = ev.get("message") or {}
            if msg.get("content"):
                content.append(msg["content"])
                if on_delta:
                    on_delta("".join(content))
            for t in msg.get("tool_calls") or []:
                fn = t.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"command": args}
                calls.append({"id": f"call_{len(calls) + 1}", "name": fn.get("name", "dm"), "arguments": args})
            if ev.get("done"):
                tokens = int(ev.get("eval_count") or 0) + int(ev.get("prompt_eval_count") or 0)
    return {"content": "".join(content), "tool_calls": calls, "tokens": tokens}


def _read_openai(resp, on_delta, cancel) -> dict:
    content, calls, tokens = [], {}, 0
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            if ev.get("usage"):
                tokens = int(ev["usage"].get("total_tokens") or 0)
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or ch.get("message") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                    if on_delta:
                        on_delta("".join(content))
                for t in delta.get("tool_calls") or []:
                    slot = calls.setdefault(t.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                    slot["id"] = t.get("id") or slot["id"]
                    fn = t.get("function") or {}
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["args"] += fn.get("arguments") or ""
    out = []
    for i, slot in sorted(calls.items()):
        try:
            args = json.loads(slot["args"] or "{}")
        except ValueError:
            args = {"command": slot["args"]}
        out.append({"id": slot["id"] or f"call_{i + 1}", "name": slot["name"] or "dm", "arguments": args})
    return {"content": "".join(content), "tool_calls": out, "tokens": tokens}
