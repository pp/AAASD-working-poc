"""The actions the model may request, and the validation applied to each request."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .tools import InvalidRequest

STRING = {"type": "string"}
INTEGER = {"type": "integer"}


def build_specs(check_names: Sequence[str]) -> List[Dict[str, Any]]:
    """Tool definitions in the format Ollama expects."""
    return [
        _spec("list_files", "List files under a path in the repository.",
              {"path": {"type": "string", "description": "Relative path. Use '.' for the root."}}, []),
        _spec("read_file", "Read a text file with line numbers.",
              {"path": STRING, "start_line": INTEGER, "end_line": INTEGER}, ["path"]),
        _spec("search", "Find lines that contain a text fragment (case-insensitive).",
              {"query": STRING, "path": {"type": "string", "description": "Optional folder to search in."}},
              ["query"]),
        _spec("edit_file",
              "Replace one exact text fragment in a file this task may change. old_string must match exactly once.",
              {"path": STRING, "old_string": STRING, "new_string": STRING},
              ["path", "old_string", "new_string"]),
        _spec("run_check", "Run one configured check in the sandbox. Returns its exit code and output.",
              {"name": {"type": "string", "enum": list(check_names)}}, ["name"]),
        _spec("finish", "Call when the change is complete. Checks are run again after you finish.",
              {"summary": STRING}, ["summary"]),
    ]


def _spec(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


class ActionValidator:
    """Checks the name and arguments of each model request before anything runs."""

    def __init__(self, specs: List[Dict[str, Any]], check_names: Sequence[str]):
        self.schemas = {s["function"]["name"]: s["function"]["parameters"] for s in specs}
        self.check_names = set(check_names)

    def validate(self, name: Any, args: Any) -> Dict[str, Any]:
        if not isinstance(name, str) or name not in self.schemas:
            raise InvalidRequest("Unknown tool {!r}. Available tools: {}".format(
                name, ", ".join(sorted(self.schemas))))
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise InvalidRequest("Arguments for {} must be a JSON object".format(name))
        schema = self.schemas[name]
        props = schema["properties"]
        unknown = set(args) - set(props)
        if unknown:
            raise InvalidRequest("{}: unexpected argument(s) {}. Valid arguments: {}".format(
                name, ", ".join(sorted(unknown)), ", ".join(props) or "(none)"))
        for req in schema.get("required", []):
            if req not in args:
                raise InvalidRequest("{}: missing required argument '{}'. Valid arguments: {}".format(
                    name, req, ", ".join(props) or "(none)"))
        for key, value in args.items():
            kind = props[key].get("type")
            if kind == "string" and not isinstance(value, str):
                raise InvalidRequest("{}: '{}' must be a string".format(name, key))
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise InvalidRequest("{}: '{}' must be an integer".format(name, key))
        if name == "run_check" and args["name"] not in self.check_names:
            raise InvalidRequest("run_check: unknown check '{}'. Allowed: {}".format(
                args["name"], ", ".join(sorted(self.check_names))))
        return args
