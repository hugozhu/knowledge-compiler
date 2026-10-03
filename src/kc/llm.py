"""Zero-dependency OpenAI-compatible client (urllib) for the local qwen-server."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path


class LLMUnavailable(RuntimeError):
    pass


class LLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        vlm_model: str,
        timeout: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.vlm_model = vlm_model
        self.timeout = timeout

    # ------------------------------------------------------------------ http
    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise LLMUnavailable(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise LLMUnavailable(f"cannot reach {url}: {e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise LLMUnavailable(f"non-JSON response: {body[:200]}") from e

    def _content(self, payload: dict) -> str:
        out = self._post("/chat/completions", payload)
        try:
            return out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMUnavailable(f"unexpected response shape: {json.dumps(out, ensure_ascii=False)[:200]}") from e

    # ---------------------------------------------------------------- public
    def chat(
        self,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 768,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        return self._content(payload)

    def ocr(self, image_path: Path, model: str | None = None, max_tokens: int = 1024) -> str:
        image_path = Path(image_path)
        suffix = image_path.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": model or self.vlm_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请识别并转录图片中的全部文字。保持原有结构与顺序，只输出文字内容，不要解释。",
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        return self._content(payload)

    def health(self) -> tuple[bool, str]:
        """Cheap GET /models probe; returns (ok, info)."""
        url = f"{self.base_url}/models"
        req = urllib.request.Request(url)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=min(self.timeout, 10)) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
                ids = ", ".join(m.get("id", "?") for m in body.get("data", []))
                return True, ids
        except Exception as e:  # noqa: BLE001
            return False, str(e)
