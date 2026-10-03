"""Configuration: KC_HOME / LLM endpoint via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SUBDIRS = (
    "inbox",
    "raw",
    "documents",
    "entities",
    "claims",
    "events",
    "projects",
    "decisions",
    "daily",
)


@dataclass
class Config:
    home: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_vlm_model: str
    llm_timeout: int = 180
    batch_chars: int = 2000

    @property
    def db_path(self) -> Path:
        return self.home / "index.db"

    def dir(self, name: str) -> Path:
        return self.home / name

    def ensure_dirs(self) -> None:
        for name in SUBDIRS:
            (self.home / name).mkdir(parents=True, exist_ok=True)


def load_config(home_override: str | None = None) -> Config:
    home = home_override or os.environ.get("KC_HOME") or str(Path.home() / "knowledge")
    return Config(
        home=Path(home).expanduser(),
        llm_base_url=os.environ.get("KC_LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
        llm_api_key=os.environ.get("KC_LLM_API_KEY", "sk-local"),
        llm_model=os.environ.get("KC_LLM_MODEL", "qwen3-4b"),
        llm_vlm_model=os.environ.get("KC_LLM_VLM_MODEL", "qwen3-vl-4b"),
        llm_timeout=int(os.environ.get("KC_LLM_TIMEOUT", "180")),
        batch_chars=int(os.environ.get("KC_BATCH_CHARS", "2000")),
    )
