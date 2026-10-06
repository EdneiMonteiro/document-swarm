"""Compile the Markdown agent declarations of a swarm into validated contracts.

A declaration stays the source of truth: persona, mission and model live in the
Markdown file the coordinator wrote.  This module turns that file into an
``AgentSpec`` and rejects, before any model call is paid for, everything the
legacy flow only discovered mid-run: duplicate names, an unknown role, a model the
session does not offer, a declaration that tries to widen the tools its role is
allowed, or a swarm that lacks the agents the cycle needs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from scripts.checks.common import InputError
from scripts.checks.lint_agents import frontmatter_text

KINDS = ("author", "reviewer", "coordinator", "rubber-duck")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
UNSET_EFFORT = {"", "standard", "padrão", "padrao", "default", "auto", "none"}
CONTEXT_TIERS = ("default", "long_context")
EVIDENCE_CLASSES = ("fact", "form")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
TOPIC_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
MAX_DECLARATION_BYTES = 256 * 1024

# Least privilege by role.  Nobody gets a tool that writes files or runs commands:
# the executor persists every artifact, so an agent never needs to.  Reading the
# swarm and, where the role verifies facts, reaching the web is all a role may do.
READ = ("view", "glob", "grep", "rg")
WEB = ("web_search", "web_fetch")
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "author": READ + WEB,
    "reviewer-fact": READ + WEB,
    "reviewer-form": READ,
    "coordinator": READ,
    "rubber-duck": READ + WEB,
}


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: str
    role: str
    model: str
    reasoning_effort: str | None
    context_tier: str | None
    evidence_class: str | None
    sources_min: int
    tools: tuple[str, ...]
    owned_topics_text: str
    body: str
    path: str
    sha256: str

    @property
    def policy(self) -> str:
        if self.kind == "reviewer":
            return "reviewer-fact" if self.evidence_class == "fact" else "reviewer-form"
        return self.kind

    def public(self) -> dict[str, Any]:
        """The fields that identify this agent in a plan or a journal; never the body."""
        return {"name": self.name, "kind": self.kind, "role": self.role, "model": self.model,
                "reasoning_effort": self.reasoning_effort, "context_tier": self.context_tier,
                "evidence_class": self.evidence_class, "sources_min": self.sources_min,
                "tools": list(self.tools), "path": self.path, "sha256": self.sha256}


@dataclass
class Compiled:
    specs: list[AgentSpec] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require(self) -> list[AgentSpec]:
        if self.errors:
            raise InputError("; ".join(self.errors))
        return self.specs

    def of(self, kind: str) -> list[AgentSpec]:
        return [spec for spec in self.specs if spec.kind == kind]

    def by_name(self, name: str) -> AgentSpec:
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise InputError(f"unknown agent {name}")


def declaration(path: Path) -> tuple[dict[str, Any], str, str]:
    """Return the frontmatter, the body and the digest of one declaration file."""
    raw = path.read_bytes()
    if len(raw) > MAX_DECLARATION_BYTES:
        raise InputError("the declaration exceeds the supported size")
    text = raw.decode("utf-8")
    front = frontmatter_text(text)
    match = FRONTMATTER.match(text)
    body = text[match.end():] if match else ""
    return front, body.strip(), hashlib.sha256(raw).hexdigest()


def tools_for(front: dict[str, Any], policy: str) -> tuple[tuple[str, ...], str | None]:
    allowed = ROLE_TOOLS[policy]
    declared = front.get("tools")
    if declared is None:
        return allowed, None
    if not isinstance(declared, list) or not all(isinstance(item, str) and item.strip() for item in declared):
        return allowed, "tools must be a list of tool names"
    extra = sorted(set(declared) - set(allowed))
    if extra:
        return allowed, f"declares tools its role may not use ({', '.join(extra)}); the role allows {', '.join(allowed)}"
    return tuple(item for item in allowed if item in declared), None


def compile_agents(swarm: Path, *, models: Iterable[str] | None = None) -> Compiled:
    """Compile every declaration under ``agents/``; collect all problems, not just the first."""
    root = swarm.resolve(strict=True)
    result = Compiled()
    available = {str(item) for item in models} if models is not None else None
    if available is None:
        result.warnings.append("model availability was not verified: no list of accepted models was supplied")
    folder = root / "agents"
    paths = sorted(folder.rglob("*.md")) if folder.is_dir() else []
    if not paths:
        result.errors.append("the swarm has no agent declarations under agents/")
        return result
    brief_swarm = None
    try:
        brief_swarm = frontmatter_text((root / "brief.md").read_text(encoding="utf-8")).get("swarm_id")
    except (OSError, UnicodeError, InputError):
        pass
    seen: dict[str, str] = {}
    for path in paths:
        where = path.relative_to(root).as_posix()
        try:
            front, body, digest = declaration(path)
        except (OSError, UnicodeError, InputError) as exc:
            result.errors.append(f"{where}: {exc}")
            continue
        problems: list[str] = []
        name, kind = front.get("name"), front.get("kind")
        if not isinstance(name, str) or not NAME.match(name):
            problems.append("name must be 1-81 characters of letters, digits, '.', '_' or '-'")
        elif name in seen:
            problems.append(f"duplicate agent name {name!r} (also {seen[name]})")
        if kind not in KINDS:
            problems.append(f"kind must be one of {', '.join(KINDS)}")
        if brief_swarm and front.get("swarm") != brief_swarm:
            problems.append(f"swarm must be {brief_swarm!r}, got {front.get('swarm')!r}")
        model = front.get("model")
        if not isinstance(model, str) or not model.strip():
            problems.append("model is required")
        elif available is not None and model != "auto" and model not in available:
            problems.append(f"model {model!r} is not offered by this session")
        effort, tier = front.get("reasoning_effort"), front.get("context_tier")
        # Coordinators write "standard" or "padrão" when the model has no effort setting; the
        # template itself says "<quando suportado>".  That is "unset", not an invalid value.
        if effort is None or (isinstance(effort, str) and effort.strip().lower() in UNSET_EFFORT):
            effort = None
        elif effort not in EFFORTS:
            problems.append(f"reasoning_effort must be one of {', '.join(EFFORTS)}")
        if tier is not None and tier not in CONTEXT_TIERS:
            problems.append(f"context_tier must be one of {', '.join(CONTEXT_TIERS)}")
        evidence = front.get("evidence_class")
        if kind == "reviewer":
            if evidence is None:
                evidence = "fact" if int(front.get("sources_min") or 0) > 0 else "form"
                result.warnings.append(f"{where}: evidence_class missing; inferred {evidence!r} from sources_min")
            if evidence not in EVIDENCE_CLASSES:
                problems.append("evidence_class must be fact or form")
        sources_min = front.get("sources_min", 0)
        if type(sources_min) is not int or sources_min < 0:
            problems.append("sources_min must be a non-negative integer")
            sources_min = 0
        # The declaration is the contract the executor enforces.  A visual author that cites
        # nothing legitimately declares 0, so falling short of the usual 5 is flagged, not refused.
        if (kind == "author" or (kind == "reviewer" and evidence == "fact")) and sources_min < 5:
            result.warnings.append(f"{where}: declares sources_min {sources_min}, below the 5 the "
                                   f"contract asks for; the declared value is what will be enforced")
        if not body:
            problems.append("the declaration has no body; the mission would be empty")
        if problems:
            result.errors.extend(f"{where}: {item}" for item in problems)
            continue
        seen[name] = where
        policy = "reviewer-fact" if kind == "reviewer" and evidence == "fact" else \
                 "reviewer-form" if kind == "reviewer" else kind
        tools, tool_problem = tools_for(front, policy)
        if tool_problem:
            result.errors.append(f"{where}: {tool_problem}")
            continue
        topics = ""
        section = re.search(r"(?ims)^#{1,3}\s*T[óo]picos\s*$(.*?)(?=^#{1,3}\s|\Z)", body)
        if section:
            topics = section.group(1).strip()
        result.specs.append(AgentSpec(
            name=name, kind=kind, role=str(front.get("role") or kind), model=model,
            reasoning_effort=effort, context_tier=tier,
            evidence_class=evidence if kind == "reviewer" else None, sources_min=sources_min,
            tools=tools, owned_topics_text=topics, body=body, path=where, sha256=digest))
    return result


def check_roster(compiled: Compiled, brief: dict[str, Any]) -> list[str]:
    """The agents a cycle needs, and the brief fields that point at them."""
    problems = []
    for kind, label in (("author", "author"), ("reviewer", "reviewer"),
                        ("coordinator", "coordinator"), ("rubber-duck", "rubber duck")):
        if not compiled.of(kind):
            problems.append(f"the swarm declares no {label}")
    if len(compiled.of("coordinator")) > 1:
        problems.append("the swarm declares more than one coordinator")
    if len(compiled.of("rubber-duck")) > 1:
        problems.append("the swarm declares more than one rubber duck")
    editorial = brief.get("editorial_reviewer")
    if editorial is not None:
        names = {spec.name: spec for spec in compiled.of("reviewer")}
        if editorial not in names:
            problems.append(f"editorial_reviewer {editorial!r} is not a declared reviewer")
        elif names[editorial].evidence_class == "fact":
            problems.append("the editorial reviewer must evaluate form, not facts")
    return problems


def parse_topics(brief: dict[str, Any]) -> dict[str, str]:
    """The topic registry the cycle is graded against, declared in the brief."""
    topics = brief.get("topics")
    if not isinstance(topics, dict) or not topics:
        raise InputError("the brief must declare topics as a mapping of id to title")
    registry: dict[str, str] = {}
    for key, title in topics.items():
        if not isinstance(key, str) or not TOPIC_ID.match(key):
            raise InputError(f"topic id {key!r} must be a short identifier")
        if not isinstance(title, str) or not title.strip():
            raise InputError(f"topic {key} needs a title")
        registry[key] = title.strip()
    return registry
