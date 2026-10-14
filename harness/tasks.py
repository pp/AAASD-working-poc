"""Task requests: what the user asks for and which files may change."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    request: str
    starting_commit: str
    allowed_write_paths: tuple
    required_checks: tuple

    @classmethod
    def load(cls, path: Path) -> "Task":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            id=raw["id"],
            title=raw["title"],
            request=raw["request"],
            starting_commit=raw["starting_commit"],
            allowed_write_paths=tuple(raw["allowed_write_paths"]),
            required_checks=tuple(raw.get("required_checks", ["acceptance", "regression_core", "regression_reversal"])),
        )

    def prompt(self) -> str:
        scope = ", ".join(self.allowed_write_paths)
        return (
            "Task: {title}\n\n{request}\n\n"
            "Files you may edit: {scope}. Do not edit any other file.\n"
            "Use run_check to run the checks. When the change is complete, call finish with a short summary."
        ).format(title=self.title, request=self.request, scope=scope)
