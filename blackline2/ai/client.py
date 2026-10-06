"""Minimaler Client für OpenAI-kompatible Chat-Schnittstellen (llama-server, Ollama …).

Bewusst ohne Proxy: Die Verbindung geht ausschließlich an den lokalen Rechner.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


class AIClientError(Exception):
    pass


_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(content: str) -> dict:
    content = _THINK.sub("", content or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except ValueError:
        pass
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except ValueError:
            pass
    # Abgeschnittene Antwort (zu lang): vollständige Einzelfunde retten
    items = []
    for m in re.finditer(r"\{[^{}]*\}", content):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(obj, dict) and "text" in obj:
            items.append(obj)
    if items:
        return {"funde": items}
    raise AIClientError("Die KI hat kein gültiges JSON geliefert.")


class ChatClient:
    def __init__(self, base_url: str, api_key: str = "", model: str = "local", timeout: float = 900):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._use_schema = True

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise AIClientError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AIClientError(f"KI nicht erreichbar: {exc}") from exc

    def chat_json(self, system: str, user: str, schema: dict | None = None,
                  max_tokens: int = 3072) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
            # Qwen3 & Co.: kein langes "Nachdenken", direkt antworten
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None and self._use_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "funde", "schema": schema, "strict": True},
            }
        try:
            data = self._post(payload)
        except AIClientError as exc:
            if "response_format" in payload and str(exc).startswith("HTTP 4"):
                # Server kennt kein JSON-Schema -> ohne erneut versuchen
                self._use_schema = False
                payload.pop("response_format")
                payload["response_format"] = {"type": "json_object"}
                try:
                    data = self._post(payload)
                except AIClientError:
                    payload.pop("response_format")
                    data = self._post(payload)
            else:
                raise
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(f"Unerwartete Antwort der KI: {str(data)[:300]}") from exc
        return extract_json(content)
