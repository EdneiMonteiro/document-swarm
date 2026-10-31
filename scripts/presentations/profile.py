"""Describe and qualify the environment a presentation delivery depends on.

The descriptors are measured, never assumed: library versions come from the
installed packages, the browser revision from the provisioned directory and the
PowerPoint build from the application itself.  A missing measurement is reported
as such and never silently replaced by a default.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, write_json_atomic
from scripts.presentations.capture import BROWSERS
from scripts.presentations.fonts import FontBook

CAPABILITY = "presentation-v1"
ROOT = Path(__file__).resolve().parents[2]
CODE = ("scripts/presentations", "schemas/presentation", "scripts/checks/presentation_contract.py",
        "scripts/checks/jsonschema_lite.py", "tests/test_presentation_contract.py",
        "tests/test_presentation_engine.py")


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def code_inventory() -> list[dict[str, Any]]:
    """Hash every file the implementation evidence is bound to."""
    files = []
    for name in CODE:
        target = ROOT / name
        if target.is_dir():
            candidates = sorted(item for item in target.rglob("*")
                                if item.is_file() and "__pycache__" not in item.parts)
        elif target.is_file():
            candidates = [target]
        else:
            raise InputError(f"the implementation inventory is incomplete: {name}")
        for item in candidates:
            files.append({"path": item.relative_to(ROOT).as_posix(), "sha256": digest_file(item),
                          "bytes": item.stat().st_size})
    return files


def library_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    result = {"python": sys.version.split()[0]}
    for package in ("python-pptx", "playwright", "Pillow", "pywin32"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "absent"
    return result


def browser_revision() -> str:
    if not BROWSERS.is_dir():
        return "absent"
    revisions = sorted(item.name for item in BROWSERS.iterdir()
                       if item.is_dir() and item.name.startswith("chromium-"))
    return revisions[-1] if revisions else "absent"


def powerpoint_build() -> dict[str, str]:
    """Read the installed PowerPoint version through the application itself."""
    if sys.platform != "win32":
        return {"status": "unsupported_platform", "version": "absent"}
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ModuleNotFoundError:
        return {"status": "automation_unavailable", "version": "absent"}
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
    except Exception as exc:  # pragma: no cover - depends on the workstation
        pythoncom.CoUninitialize()
        return {"status": "not_launchable", "version": "absent", "detail": str(exc)[:200]}
    try:
        return {"status": "available", "version": str(application.Version),
                "build": str(application.Build), "name": str(application.Name)}
    finally:
        try:
            application.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def environment() -> dict[str, Any]:
    return {
        "schema_version": 1, "capability": CAPABILITY,
        "operating_system": {"system": platform.system(), "release": platform.release(),
                             "version": platform.version(), "machine": platform.machine()},
        "libraries": library_versions(), "chromium": browser_revision(),
        "powerpoint": powerpoint_build(),
        "fonts": FontBook().descriptors(),
    }


def write_records(reports: Path, *, implementation_checks: list[dict[str, Any]],
                  profile_checks: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    """Write the implementation and profile evidence plus their small index files."""
    reports.mkdir(parents=True, exist_ok=True)
    implementation_evidence = {"schema_version": 1, "capability": CAPABILITY,
                               "files": code_inventory(), "checks": implementation_checks}
    profile_evidence = {"schema_version": 1, "capability": CAPABILITY, "profile": profile,
                        "environment": environment(), "checks": profile_checks}
    write_json_atomic(reports / "implementation-evidence.json", implementation_evidence)
    write_json_atomic(reports / "profile-evidence.json", profile_evidence)
    implementation = {"schema_version": 1, "capability": CAPABILITY,
                      "evidence": {"path": "reports/implementation-evidence.json",
                                   "sha256": digest_file(reports / "implementation-evidence.json")}}
    profile_record = {"schema_version": 1, "capability": CAPABILITY, "profile": profile,
                      "evidence": {"path": "reports/profile-evidence.json",
                                   "sha256": digest_file(reports / "profile-evidence.json")}}
    write_json_atomic(reports / "implementation.json", implementation)
    write_json_atomic(reports / "profile.json", profile_record)
    return {"implementation": digest_file(reports / "implementation.json"),
            "profile": digest_file(reports / "profile.json")}
