"""A stand-in for the ``copilot`` command, used only by the tests.

It accepts the flags the backend passes, reads the prompt from stdin like the real CLI does when no ``-p`` is
given, records exactly how it was called and answers with a valid result for whatever task the prompt describes.
Behaviour switches come from the environment, so one script covers success, failure, wrappers, delays and echo.

    FAKE_COPILOT_LOG      file that receives one JSON line per invocation
    FAKE_COPILOT_ROOT     the swarm the scripted agent writes against
    FAKE_COPILOT_BASE     base URL of the test source server
    FAKE_COPILOT_MODE     scripted (default) | echo
    FAKE_COPILOT_FAIL     substring: exit 1 the first time a prompt contains it (needs FAKE_COPILOT_STATE)
    FAKE_COPILOT_STATE    folder for the markers the switches above need
    FAKE_COPILOT_SLEEP    seconds to wait before answering
    FAKE_COPILOT_WRAP     fence | prose | none (default)
    FAKE_COPILOT_EMPTY    1: print nothing
    FAKE_COPILOT_MODEL    model name written to the usage file instead of the requested one
    FAKE_COPILOT_GRADES   JSON {"cycle|reviewer|topic": "B+"} for the scripted reviewers
    FAKE_COPILOT_DISOBEY  1: the no-write probe really writes the file (a CLI that ignores its tool restriction)
    FAKE_COPILOT_LEAK     1: the confinement probe really reads the file outside the working folder (a CLI that
                          ignores its path restriction)
    FAKE_COPILOT_SILENT   name of one qualification probe the CLI answers with nothing at all
    FAKE_COPILOT_TITLE    the page title the web probe reports
    FAKE_COPILOT_CHILD    1: start a child process that outlives its parent unless the whole tree is killed;
                          orphan: start one whose parent is already gone, holding the CLI's pipes open
    FAKE_COPILOT_FAIL_WITH_OUTPUT  1: print a valid answer and still exit 1
    FAKE_COPILOT_MCP_LIST text that `mcp list` prints (default: no servers); "fail" exits 1, as an old CLI would
    FAKE_COPILOT_MCP_LOG  file that receives one JSON line per `mcp list` run, which is not an agent call
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_orchestration_engine import Scripted  # noqa: E402  (needs the path above)

FLAGS_WITH_VALUE = {"--model", "--reasoning-effort", "--context", "--usage-output-file", "--deny-tool",
                    "--disable-mcp-server"}
LISTS = {"--available-tools"}


def append_line(path: str, text: str) -> None:
    """Append one line to a log that agents starting at the same moment all write to.

    On Windows an append is a seek to the end followed by a write, so two processes appending at once can overwrite each
    other and leave a line of zero bytes behind.  Appends are taken one at a time through a lock file created exclusively,
    and a lock left by a process that was killed while holding it is broken after a few seconds.
    """
    lock = path + ".lock"
    deadline = time.time() + 5
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() > deadline:
                try:
                    os.unlink(lock)
                except OSError:
                    pass
                deadline = time.time() + 5
            time.sleep(0.005)
    try:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text + "\n")
    finally:
        os.close(handle)
        try:
            os.unlink(lock)
        except OSError:
            pass


def parse(argv: list[str]) -> dict:
    parsed: dict = {"flags": [], "deny": [], "tools": []}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in LISTS:
            index += 1
            while index < len(argv) and not argv[index].startswith("-"):
                parsed["tools"].append(argv[index])
                index += 1
            continue
        if item in FLAGS_WITH_VALUE:
            value = argv[index + 1]
            if item == "--deny-tool":
                parsed["deny"].append(value)
            else:
                parsed[item.lstrip("-").replace("-", "_")] = value
            index += 2
            continue
        parsed["flags"].append(item)
        index += 1
    return parsed


def section(prompt: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", prompt, re.M | re.S)
    return match.group(1) if match else ""


def topics_of(text: str) -> dict:
    return {key: title.strip() for key, title in re.findall(r"^- ([A-Za-z0-9_-]+): (.*)$", text, re.M)}


def task_from(prompt: str) -> dict:
    header = re.search(r"ciclo (\d+) · rodada (\d+) · tarefa (\S+) · tentativa (\d+)", prompt)
    who = re.search(r"Você é o agente `([^`]+)` \(papel: ([a-z-]+)\)", prompt)
    blocks = re.findall(r"````json\n(.*?)\n````", prompt, re.S)
    schema = json.loads(blocks[-1])
    properties = schema["properties"]
    kind = {"author": "author", "reviewer": "reviewer", "rubber-duck": "rubber-duck"}.get(who.group(2))
    if kind is None:
        kind = "consolidation" if "document_markdown" in properties else "narrative"
    context: dict = {}
    if kind == "author":
        context = {"source_prefix": re.search(r"Cada `id` é `(F\d+)`", prompt).group(1),
                   "topics": topics_of(section(prompt, "Tópicos do documento"))}
    if kind == "reviewer":
        context = {"topics": topics_of(section(prompt, "Tópicos a avaliar")), "editorial": "editorial" in properties,
                   "evidence_class": "fact" if "sources_consulted" in properties else "form"}
    return {"task_id": header.group(3), "kind": kind, "agent": who.group(1), "cycle": int(header.group(1)),
            "round": int(header.group(2)), "attempt": int(header.group(4)), "context": context, "prompt": prompt}


def answer_probe(name: str, prompt: str) -> int:
    """Answer the qualification probes like a CLI that honours its flags (or, when told, one that does not)."""
    if os.environ.get("FAKE_COPILOT_SILENT") == name:
        return 0
    if name.startswith("contract") or name == "large":
        print(json.dumps({"ok": True, "soma": 7}))
    elif name == "no-write":
        path = re.search(r"arquivo `([^`]+)`", prompt).group(1)
        if os.environ.get("FAKE_COPILOT_DISOBEY") == "1":
            Path(path).write_text("x", encoding="utf-8")
            print(json.dumps({"created": True}))
        else:
            print(json.dumps({"created": False}))
    elif name == "confined":
        path = re.search(r"arquivo `([^`]+)`", prompt).group(1)
        leaked = Path(path).read_text(encoding="utf-8") if os.environ.get("FAKE_COPILOT_LEAK") == "1" else None
        print(json.dumps({"content": leaked}))
    elif name == "web":
        print(json.dumps({"title": os.environ.get("FAKE_COPILOT_TITLE", "Example Domain")}))
    return 0


def list_mcp() -> int:
    """What ``copilot mcp list`` prints.  It is not an agent call, so it never reaches the call log."""
    log = os.environ.get("FAKE_COPILOT_MCP_LOG")
    if log:
        append_line(log, json.dumps({"mcp_list": sys.argv[1:], "cwd": os.getcwd()}))
    listing = os.environ.get("FAKE_COPILOT_MCP_LIST", "No MCP servers configured.\n")
    if listing == "fail":
        # A listing that looks right and an exit status that says it is not: the status is what must decide.
        sys.stdout.write("User servers:\n  playwright (local)\n")
        print("simulated failure", file=sys.stderr)
        return 1
    sys.stdout.write(listing)
    return 0


def main() -> int:
    if sys.argv[1:3] == ["mcp", "list"]:
        return list_mcp()
    parsed = parse(sys.argv[1:])
    prompt = sys.stdin.buffer.read().decode("utf-8")
    log = os.environ.get("FAKE_COPILOT_LOG")
    if log:
        append_line(log, json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "entries": sorted(os.listdir(".")),
                                     "prompt_bytes": len(prompt.encode("utf-8")),
                                     "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                                     "pid": os.getpid(), "at": time.time()}))
    delay = float(os.environ.get("FAKE_COPILOT_SLEEP", "0") or 0)
    if os.environ.get("FAKE_COPILOT_CHILD") == "1" and log:
        child = subprocess.Popen([sys.executable, "-S", "-c", "import time; time.sleep(120)"])
        append_line(log, json.dumps({"child": child.pid}))
    if os.environ.get("FAKE_COPILOT_CHILD") == "orphan" and log:
        # The middle process exits at once, so its child has no parent left for a tree kill to follow,
        # yet it still holds the pipes of the CLI open.
        subprocess.run([sys.executable, "-S", "-c",
                        "import json, subprocess, sys; "
                        "p = subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(40)']); "
                        "open(sys.argv[1], 'a').write(json.dumps({'child': p.pid}) + chr(10))", log])
    if delay:
        time.sleep(delay)
    needle, state = os.environ.get("FAKE_COPILOT_FAIL"), os.environ.get("FAKE_COPILOT_STATE")
    if needle and state and needle in prompt:
        marker = Path(state) / hashlib.sha256(needle.encode("utf-8")).hexdigest()
        if not marker.exists():
            marker.write_text("failed once", encoding="utf-8")
            print("simulated failure", file=sys.stderr)
            return 1
    usage = parsed.get("usage_output_file")
    if usage:
        model = os.environ.get("FAKE_COPILOT_MODEL") or parsed.get("model") or "modelo-resolvido"
        Path(usage).write_text(json.dumps({"modelMetrics": {model: {"requests": {"count": 1}}}, "totalApiDurationMs": 7}),
                               encoding="utf-8")
    if os.environ.get("FAKE_COPILOT_EMPTY") == "1":
        return 0
    if os.environ.get("FAKE_COPILOT_MODE") == "echo":
        print(json.dumps({"bytes": len(prompt.encode("utf-8")), "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}))
        return 0
    probe = re.search(r"^Identificador do teste: ([a-z-]+)", prompt, re.M)
    if probe:
        return answer_probe(probe.group(1), prompt)
    grades = {}
    for key, value in json.loads(os.environ.get("FAKE_COPILOT_GRADES") or "{}").items():
        cycle, reviewer, topic = key.split("|")
        grades[(int(cycle), reviewer, topic)] = value
    agent = Scripted(Path(os.environ["FAKE_COPILOT_ROOT"]), os.environ["FAKE_COPILOT_BASE"], grades=grades)
    text = json.dumps(agent(task_from(prompt)), ensure_ascii=False)
    wrap = os.environ.get("FAKE_COPILOT_WRAP", "none")
    if wrap == "fence":
        text = f"Aqui está o resultado:\n```json\n{text}\n```\n"
    elif wrap == "prose":
        text = f"Segue a entrega. {text} Qualquer dúvida, avise."
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 1 if os.environ.get("FAKE_COPILOT_FAIL_WITH_OUTPUT") == "1" else 0


if __name__ == "__main__":
    raise SystemExit(main())
