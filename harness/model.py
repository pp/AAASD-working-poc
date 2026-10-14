"""Ollama chat client and tool-call extraction."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple


class ModelError(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout_s: int, num_ctx: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx

    def check_model(self) -> None:
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise ModelError("Ollama is not reachable at {}: {}".format(self.base_url, exc))
        names = {m.get("name") for m in data.get("models", [])}
        if self.model not in names:
            raise ModelError("Model '{}' is not installed. Run: ollama pull {}".format(self.model, self.model))

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ModelError("model request failed: {}".format(exc))
        message = data.get("message")
        if not isinstance(message, dict):
            raise ModelError("model reply had no message")
        return message


def _as_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except ValueError:
        return None


def extract_tool_calls(message: Dict[str, Any]) -> Tuple[List[Tuple[Any, Any]], bool]:
    """Return ([(name, arguments)], from_text). Falls back to a JSON tool call written as text."""
    calls: List[Tuple[Any, Any]] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                pass
        calls.append((fn.get("name"), args))
    if calls:
        return calls, False
    parsed = _as_json(message.get("content") or "")
    if isinstance(parsed, dict) and isinstance(parsed.get("name"), str):
        args = parsed.get("arguments", parsed.get("parameters", {}))
        return [(parsed["name"], args)], True
    return [], False
