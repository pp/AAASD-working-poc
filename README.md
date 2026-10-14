# Coding harness (Stage 1 proof of concept)

A small Python application that takes a bug-fix request, lets a local Ollama model
change a disposable copy of a repository through a fixed set of tools, runs checks in
a Docker sandbox, and shows the changed files, the diff, and the check results.

The target is [OpenStock](https://github.com/TMBeaver/openstock), at commit
`abc87dccab96c78917b35add542284e61adc5f37`.

## Layout

```
harness/
  __main__.py      python -m harness
  cli.py           run command, progress output, final result
  config.py        settings (no secrets)
  tasks.py         task request: text, starting commit, allowed write paths
  workspace.py     disposable copy of the target repo; git status/diff on the copy
  tools.py         repository tools: list_files, read_file, search, edit_file
  actions.py       tool definitions for the model and request validation
  model.py         Ollama client and tool-call extraction
  controller.py    agent loop, limits, counters, final status
  sandbox.py       Docker execution of configured checks (ExecutionEnvironment)
  acceptance/      acceptance check, mounted read-only into the sandbox
  tasks/           task definitions
  tests/           unit tests (no Ollama, Docker, or API key needed)
  harness.example.json   example settings
```

## Setup

Requirements: Python 3.9+, Docker (daemon running), Ollama with a model, git.
The harness uses only the standard library, so there is nothing to `pip install`.

```sh
cp harness.example.json harness.json      # optional; defaults are the same
ollama pull llama3.2:3b                   # or set "model" in harness.json
docker pull python:3.12-slim
```

Get the target repository and seed its demo database (the acceptance check and the
regression suite need a database with products):

```sh
git clone https://github.com/TMBeaver/openstock.git ../openstock
cd ../openstock && git checkout abc87dccab96c78917b35add542284e61adc5f37
PYTHONPATH=. python3 seed_demo.py --password choose-a-password   # writes data/openstock.db
```

## Run

```sh
cd harness
python3 -m harness run --task tasks/negative_sale_quantity.json --repo ../openstock
```

Each run creates `runs/<timestamp>-<task>/` containing:

- `repo/`: the disposable copy the agent edits (includes `.git`)
- `events.jsonl`: every model step, tool call, and check
- `diff.patch` and `report.md`

Exit code is 0 only when the agent finished and all required checks passed.

## Tests

```sh
python3 -m unittest discover -s tests -t .                       # unit tests, no Docker needed
HARNESS_TARGET_REPO=../openstock python3 -m unittest tests.test_sandbox tests.test_acceptance
```

`test_sandbox` runs real containers and is skipped if Docker is not running.
`test_acceptance` checks that the acceptance check fails on the starting commit and
passes after a reference fix. It is skipped unless `HARNESS_TARGET_REPO` is set.

## How it works

1. The workspace is a copy of the target repo at the task's starting commit. The copy is checked before the run.
2. The acceptance check runs once on the untouched copy. It is expected to fail. This result is recorded.
3. The controller sends the task and the tool definitions to the model. Each reply is parsed for tool calls.
4. Every request goes through validation before anything runs:
   - unknown tool or wrong or missing arguments → error text goes back to the model; nothing runs
   - a path outside the repo, a denied folder (`.git`, `data`, …), or an edit outside the task's allowed files → denied, counted
   - `run_check` accepts only the configured check names
5. Allowed actions run and their results go back to the model as tool messages.
6. The loop stops on `finish`, the action limit, or too many replies without a tool call or model errors.
7. After the loop the controller runs all required checks again, collects the changed files and the diff, and sets the status. A check that failed or could not run is shown as such. The model's claim alone never produces `PASSED`.

## Security boundaries

- **Repository copy:** the agent works only on a copy in `runs/`. The target clone is not modified.
- **File tools:** paths are resolved with symlinks followed and must stay inside the copy. Denied folders are never read or written. Edits are limited to `allowed_write_paths` from the task.
- **Command execution:** checks run only as configured commands inside a container with:
  - `--network none` (no network access)
  - `--read-only` root filesystem; the only writable mounts are the workspace copy and a 256 MB `/tmp`
  - the acceptance folder mounted read-only
  - `--cap-drop ALL`, `no-new-privileges`, memory and process limits
  - the host user's UID/GID, so the workspace files stay owned by you
- **Stopping:** a check that exceeds `check_timeout_s` is stopped and its container is removed with `docker rm -f`, which also ends its child processes. Ctrl-C removes all containers started by the run.
- **No push, merge, or deploy:** the agent has no git tool and no network. The harness never runs git write commands.
- **Credentials:** the harness reads no secrets. The sandbox gets no host environment. `config_local.json` and `.env` are denied paths.

## Limits and counters

| Setting | Default | Effect |
|---|---|---|
| `max_actions` | 25 | Stops after this many tool requests |
| `max_retries` | 3 | Stops after this many model errors or replies without a tool call |
| `max_output_chars` | 4000 | Check output is cut to this size; the cut is marked |
| `max_read_chars` | 6000 | `read_file` output is cut to this size; the cut is marked |
| `max_search_hits` / `max_list_entries` | 50 / 200 | Search and listing are capped and marked |
| `check_timeout_s` | 300 | Stops a stuck check |

The run prints and records: actions, invalid requests, denied actions, model retries,
and shortened outputs.

## Known limitations

- The model is a small local model (`llama3.2:3b`). It may not complete the fix; the status will say so.
- Checks run in `python:3.12-slim` with no extra packages. Regression suites that need Flask or reportlab would need an image that installs them.
- The regression suite needs a database with products. The harness copies `data/openstock.db` from the target clone, so seed it first.
