"""Agent backends: how a task from the engine becomes an agent run.

A backend is a callable ``backend(task) -> Answer``.  ``CopilotCli`` runs each task as one non-interactive
Copilot CLI process, so a swarm needs no coordinator model: the engine says what is needed, this module runs
the agents in parallel, and the engine records what comes back.

What an agent may do is decided here, by flags the CLI enforces, not by the prompt:

* only the tools of its role exist (``--available-tools``), and shell and file writes are denied on top of
  that, because a denial wins over every allowance;
* the working directory is an empty scratch folder, outside the swarm, and custom instructions and questions
  to the user are switched off;
* the prompt travels on stdin, which the CLI reads when no ``-p`` is given, so its size is bounded by memory
  and not by the command line (the Windows limit is about 32 thousand characters, a document is more).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from scripts.checks.common import InputError
from scripts.orchestration.contracts import Answer

WEB_TOOLS = {"web_fetch", "web_search"}
DENIED = ("shell", "write")
STDERR_TAIL = 400
MCP_LIST_TIMEOUT = 60
MCP_SECTION = re.compile(r"^([A-Za-z][A-Za-z ]*) servers:\s*$")
MCP_ENTRY = re.compile(r"^\s+([A-Za-z0-9][A-Za-z0-9._-]{0,99}) \([^)]*\)\s*$")
BUILTIN_ORIGIN = "builtin"


def parse_mcp_list(text: str) -> dict[str, list[str]]:
    """The MCP servers ``copilot mcp list`` names, by origin; empty if the text is not that listing.

    The listing is a few headings ("User servers:", "Plugin servers:", "Builtin servers:") with one
    ``name (type)`` line under each.  Anything else is not trusted: an empty result means "do not prune".
    """
    found: dict[str, list[str]] = {}
    origin = ""
    for line in text.splitlines():
        heading = MCP_SECTION.match(line)
        if heading:
            origin = heading.group(1).strip().lower()
            continue
        entry = MCP_ENTRY.match(line)
        if entry and origin:
            found.setdefault(origin, []).append(entry.group(1))
    return found


def mcp_flags(tools: Sequence[str], servers: dict[str, list[str]]) -> list[str]:
    """The flags that stop every configured MCP server the task has no tool of from starting.

    A server is started for each agent process and costs seconds before the model is asked anything (measured:
    a trivial call took a median of 44 s with six user, five plugin and two builtin servers, and 11.5 s without).
    ``--available-tools`` already hides the tools of the others, so stopping them takes nothing from the agent.
    An MCP tool is named ``<server>-<tool>``, so a task that lists one keeps its server.
    """
    def needed(name: str) -> bool:
        return any(tool == name or tool.startswith(f"{name}-") for tool in tools)

    flags: list[str] = []
    builtin = servers.get(BUILTIN_ORIGIN, [])
    if builtin and not any(needed(name) for name in builtin):
        flags.append("--disable-builtin-mcps")
    seen: set[str] = set()
    for origin, names in servers.items():
        for name in names:
            if origin != BUILTIN_ORIGIN and name not in seen and not needed(name):
                seen.add(name)
                flags += ["--disable-mcp-server", name]
    return flags


def models_from_usage(data: Any) -> list[str]:
    """The model identifiers a usage record mentions, wherever the CLI nests them.

    The record is the CLI's own account of what ran.  Its shape is not documented, so this reads it
    loosely and the raw file is always kept next to the result for whoever needs the exact figures.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower().replace("_", "")
                if lowered in ("model", "modelid") and isinstance(item, str):
                    found.add(item)
                elif lowered in ("models", "modelmetrics", "bymodel") and isinstance(item, dict):
                    found.update(str(name) for name in item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return sorted(found)


class CopilotCli:
    """Runs one engine task as one non-interactive ``copilot`` process."""

    name = "copilot-cli"

    def __init__(self, *, executable: Sequence[str] | None = None, timeout: float = 3600.0,
                 usage_dir: Path | None = None, environment: dict[str, str] | None = None,
                 drain_timeout: float = 15.0, prune_mcp: bool = True) -> None:
        self.executable = list(executable) if executable else self.discover()
        self.timeout = timeout
        self.drain_timeout = drain_timeout
        self.usage_dir = usage_dir
        self.environment = environment or {}
        self.prune_mcp = prune_mcp
        self.mcp_guard = threading.Lock()
        self.mcp_found: dict[str, list[str]] | None = None
        self.running: dict[int, subprocess.Popen[bytes]] = {}
        self.guard = threading.Lock()
        self.cancelled = threading.Event()

    @staticmethod
    def discover() -> list[str]:
        if os.environ.get("DOCSWARM_NO_REAL_CLI") == "1":
            # Test runs set this so that no mistake in a test, and no deliberately broken guard in a
            # mutation run, can ever reach a model and spend credits.
            raise InputError("the real copilot CLI is disabled in this environment (DOCSWARM_NO_REAL_CLI=1)")
        path = shutil.which("copilot")
        if not path:
            raise InputError("the copilot command is not on PATH; install the GitHub Copilot CLI or pass --copilot")
        return [path]

    # -- the command ---------------------------------------------------------
    def mcp_servers(self) -> dict[str, list[str]]:
        """The MCP servers this CLI would start for an agent, asked once; empty when pruning is off or unknown."""
        if not self.prune_mcp:
            return {}
        with self.mcp_guard:
            if self.mcp_found is None:
                self.mcp_found = self.list_mcp()
            return self.mcp_found

    def list_mcp(self) -> dict[str, list[str]]:
        # Listed from an empty folder, like the agents run, so that a workspace file cannot change the answer.
        work = tempfile.mkdtemp(prefix="docswarm-mcp-")
        try:
            done = subprocess.run([*self.executable, "mcp", "list"], stdin=subprocess.DEVNULL, capture_output=True,
                                  cwd=work, env={**os.environ, "NO_COLOR": "1", **self.environment},
                                  timeout=MCP_LIST_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return {}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return parse_mcp_list(done.stdout.decode("utf-8", errors="replace")) if done.returncode == 0 else {}

    def command(self, task: dict[str, Any], usage_file: Path | None = None,
                mcp_servers: dict[str, list[str]] | None = None) -> list[str]:
        """The command line for one task, without the prompt (which goes on stdin).

        Building it starts nothing: the MCP servers to stop are given by whoever is about to run or show the task
        (``mcp_servers()``), so that describing a command can never run the CLI.
        """
        tools = [str(item) for item in task.get("tools") or []]
        if not tools:
            raise InputError(f"task {task.get('task_id')} has no tools; an empty list would mean no restriction")
        args = [*self.executable, "-s", "--no-ask-user", "--no-custom-instructions", "--no-color", "--no-auto-update",
                "--disallow-temp-dir", "--allow-all-tools"]
        for denied in DENIED:
            args += ["--deny-tool", denied]
        args += ["--available-tools", *tools]
        if WEB_TOOLS & set(tools):
            args.append("--allow-all-urls")
        if task.get("model"):
            args += ["--model", str(task["model"])]
        if task.get("reasoning_effort"):
            args += ["--reasoning-effort", str(task["reasoning_effort"])]
        if task.get("context_tier"):
            args += ["--context", str(task["context_tier"])]
        if usage_file is not None:
            args += ["--usage-output-file", str(usage_file)]
        args += mcp_flags(tools, mcp_servers or {})
        return args

    # -- running -------------------------------------------------------------
    @staticmethod
    def group_flags() -> dict[str, Any]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    def kill(process: subprocess.Popen[bytes]) -> None:
        """End the process and everything it started; a lone ``kill`` leaves its children running."""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=30)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.kill()
        except OSError:
            pass

    def drain(self, process: subprocess.Popen[bytes]) -> None:
        """Collect what is left of a stopped process, without waiting on a pipe a surviving child holds open.

        Closing the pipes here would block until the survivor lets go, so after the wait they are left alone:
        the reader threads are daemons and end with the survivor.
        """
        try:
            process.communicate(timeout=self.drain_timeout)
        except subprocess.TimeoutExpired:
            pass

    def cancel(self) -> None:
        """Stop every agent that is running and refuse new ones."""
        self.cancelled.set()
        with self.guard:
            processes = list(self.running.values())
        for process in processes:
            self.kill(process)

    def __call__(self, task: dict[str, Any]) -> Answer:
        if self.cancelled.is_set():
            return Answer(None, {"error": "cancelled"})
        started = time.monotonic()
        usage_file = None
        if self.usage_dir is not None:
            self.usage_dir.mkdir(parents=True, exist_ok=True)
            usage_file = self.usage_dir / f"{task['label']}.json"
        argv = self.command(task, usage_file, self.mcp_servers())
        work = tempfile.mkdtemp(prefix="docswarm-agent-")
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=work, env={**os.environ, "NO_COLOR": "1", **self.environment},
                                       **self.group_flags())
        except OSError as exc:
            shutil.rmtree(work, ignore_errors=True)
            return Answer(None, {"error": f"cannot start the CLI: {type(exc).__name__}"})
        with self.guard:
            self.running[process.pid] = process
        try:
            try:
                out, err = process.communicate(task["prompt"].encode("utf-8"), timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.kill(process)
                self.drain(process)
                return Answer(None, {"error": "timeout", "seconds": round(time.monotonic() - started, 1)})
        finally:
            with self.guard:
                self.running.pop(process.pid, None)
            shutil.rmtree(work, ignore_errors=True)
        runtime: dict[str, Any] = {"seconds": round(time.monotonic() - started, 1), "exit_code": process.returncode,
                                   "backend": self.name}
        models = self.read_models(usage_file)
        if models:
            runtime["models_seen"] = ",".join(models)
        if task.get("model") and task["model"] != "auto" and models and task["model"] not in models:
            runtime["model_mismatch"] = True
        if usage_file is not None and usage_file.is_file():
            runtime["usage_file"] = usage_file.name
        text = out.decode("utf-8", errors="replace").strip()
        if self.cancelled.is_set():
            runtime["error"] = "cancelled"
            return Answer(None, runtime)
        if process.returncode != 0 or not text:
            tail = err.decode("utf-8", errors="replace").strip()[-STDERR_TAIL:]
            runtime["error"] = f"exit {process.returncode}" if process.returncode else "empty output"
            if tail:
                runtime["stderr"] = tail
            return Answer(None, runtime)
        runtime["output_chars"] = len(text)
        return Answer(text, runtime)

    @staticmethod
    def read_models(usage_file: Path | None) -> list[str]:
        if usage_file is None or not usage_file.is_file():
            return []
        try:
            return models_from_usage(json.loads(usage_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []
