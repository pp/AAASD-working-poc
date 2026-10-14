"""Settings for the harness. Keep secrets out of this file."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_CHECKS: Dict[str, List[str]] = {
    "acceptance": ["python", "/acceptance/check_sale_quantity.py"],
    "regression_core": ["python", "tests/test_core.py"],
    "regression_reversal": ["python", "tests/test_reversal_advances.py"],
}

DEFAULT_DENIED = (
    ".git", "data", "backups", "documents", "logs", "venv", ".venv", "vendor",
    "__pycache__", "config_local.json", ".env",
)


@dataclass
class Settings:
    runs_dir: Path = Path("runs")
    ollama_url: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    model_timeout_s: int = 300
    num_ctx: int = 8192
    max_actions: int = 25
    max_retries: int = 3
    max_output_chars: int = 4000
    max_read_chars: int = 6000
    max_list_entries: int = 200
    max_search_hits: int = 50
    docker_image: str = "python:3.12-slim"
    check_timeout_s: int = 300
    container_memory: str = "1g"
    container_pids: int = 256
    checks: Dict[str, List[str]] = field(default_factory=lambda: dict(DEFAULT_CHECKS))
    denied_paths: Tuple[str, ...] = DEFAULT_DENIED

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError("Unknown settings: {}".format(", ".join(sorted(unknown))))
        if "runs_dir" in raw:
            raw["runs_dir"] = Path(raw["runs_dir"])
        if "denied_paths" in raw:
            raw["denied_paths"] = tuple(raw["denied_paths"])
        return cls(**raw)
