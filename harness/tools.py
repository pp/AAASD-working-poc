"""Repository tools. Every path is checked here, not by the model or the prompt."""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

TEXT_SUFFIXES = (".py", ".html", ".md", ".txt", ".json", ".css", ".js", ".sql", ".toml", ".cfg", ".ini")
MAX_FILE_BYTES = 2 * 1024 * 1024


class ToolError(Exception):
    """A request that failed. The message is returned to the model."""


class InvalidRequest(ToolError):
    """Bad tool name or arguments. Nothing was executed."""


class DeniedAction(ToolError):
    """A path or permission rule refused the action."""


class RepositoryTools:
    def __init__(self, root: Path, allowed_write_paths: Iterable[str], denied_paths: Iterable[str],
                 max_read_chars: int, max_list_entries: int, max_search_hits: int):
        self.root = Path(root).resolve()
        self.allowed_write = tuple(p.strip("/") for p in allowed_write_paths)
        self.denied = frozenset(denied_paths)
        self.max_read_chars = max_read_chars
        self.max_list_entries = max_list_entries
        self.max_search_hits = max_search_hits
        self.shortened = 0

    # ── path rules ───────────────────────────────────────────
    def resolve(self, rel: str, for_write: bool = False) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise InvalidRequest("path must be a non-empty string")
        p = Path(rel.strip())
        if p.is_absolute() or ".." in p.parts:
            raise DeniedAction("path '{}' is outside the repository".format(rel))
        target = (self.root / p).resolve()
        try:
            inside = target.relative_to(self.root)
        except ValueError:
            raise DeniedAction("path '{}' resolves outside the repository".format(rel))
        if any(part in self.denied for part in inside.parts):
            raise DeniedAction("path '{}' is not accessible to the agent".format(rel))
        if for_write and not self._writable(inside.as_posix()):
            raise DeniedAction("'{}' is not in the files this task may change".format(inside.as_posix()))
        return target

    def _writable(self, rel_posix: str) -> bool:
        return any(rel_posix == a or rel_posix.startswith(a + "/") for a in self.allowed_write)

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ── tools ────────────────────────────────────────────────
    def list_files(self, path: str = ".") -> str:
        base = self.resolve(path)
        if not base.is_dir():
            raise InvalidRequest("'{}' is not a directory".format(path))
        entries: List[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in self.denied and not d.startswith("."))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if name.endswith(".pyc") or name in self.denied:
                    continue
                entries.append(self._rel(full))
                if len(entries) >= self.max_list_entries:
                    self.shortened += 1
                    return "\n".join(entries) + "\n[listing shortened at {} entries]".format(self.max_list_entries)
        return "\n".join(entries) if entries else "(no files)"

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise InvalidRequest("'{}' is not a file".format(path))
        if target.stat().st_size > MAX_FILE_BYTES:
            raise InvalidRequest("'{}' is larger than {} bytes".format(path, MAX_FILE_BYTES))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        out = []
        size = 0
        for n in range(start, end + 1):
            row = "{:5d} | {}".format(n, lines[n - 1])
            size += len(row) + 1
            if size > self.max_read_chars:
                self.shortened += 1
                out.append("[file shortened: showing lines {}-{} of {}; use start_line to continue]".format(
                    start, n - 1, len(lines)))
                break
            out.append(row)
        return "\n".join(out) if out else "(empty range)"

    def search(self, query: str, path: str = ".") -> str:
        if not isinstance(query, str) or not query:
            raise InvalidRequest("query must be a non-empty string")
        base = self.resolve(path)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        hits: List[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in self.denied and not d.startswith("."))
            for name in sorted(filenames):
                if not name.endswith(TEXT_SUFFIXES) or name in self.denied:
                    continue
                full = Path(dirpath) / name
                try:
                    if full.stat().st_size > MAX_FILE_BYTES:
                        continue
                    text = full.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for n, line in enumerate(text, 1):
                    if pattern.search(line):
                        hits.append("{}:{}: {}".format(self._rel(full), n, line.strip()[:200]))
                        if len(hits) >= self.max_search_hits:
                            self.shortened += 1
                            return "\n".join(hits) + "\n[search shortened at {} hits]".format(self.max_search_hits)
        return "\n".join(hits) if hits else "(no matches)"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        target = self.resolve(path, for_write=True)
        if not target.is_file():
            raise InvalidRequest("'{}' is not a file".format(path))
        if not isinstance(old_string, str) or old_string == "":
            raise InvalidRequest("old_string must be a non-empty string")
        if not isinstance(new_string, str):
            raise InvalidRequest("new_string must be a string")
        with target.open("r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        count = text.count(old_string)
        if count == 0:
            raise InvalidRequest("old_string was not found in '{}'; copy the exact text from read_file".format(path))
        if count > 1:
            raise InvalidRequest("old_string matches {} times in '{}'; include more surrounding lines".format(count, path))
        updated = text.replace(old_string, new_string, 1)
        if target.suffix == ".py":
            try:
                ast.parse(updated, filename=path)
            except SyntaxError as exc:
                raise InvalidRequest(
                    "edit refused: the result would not be valid Python ({} at line {}). '{}' was not changed.".format(
                        exc.msg, exc.lineno, path))
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        return "Edited {}: replaced 1 occurrence".format(path)
