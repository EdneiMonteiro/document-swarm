"""Record the acceptance of one candidate and copy it without partial results.

The acceptance enumerates the public files of the layers below it and is written
only after the recorded gate approved the current review.  Copying uses an
exclusive temporary directory in the destination filesystem and promotes it to a
new name, so an interrupted copy never becomes a delivery.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, load_data, parse_strict_json, write_json_atomic
from scripts.checks.presentation_contract import CAPABILITY, Artifacts, logical
from scripts.presentations.model import digest

REPORT_ROLES = ("inputs", "manifest", "inspections", "editorial_text", "review", "gate")


def descriptor(root: Path, relative: str) -> dict[str, Any]:
    path = root.joinpath(*logical(relative, "acceptance entry").parts)
    if not path.is_file():
        raise InputError(f"the acceptance set references a missing file: {relative}")
    return {"path": relative, "sha256": digest(path), "bytes": path.stat().st_size}


def acceptance(swarm: Path, cycle: int) -> dict[str, Any]:
    """Build the terminal acceptance record from the already verified layers."""
    swarm = swarm.resolve(strict=True)
    reports = swarm / "reports"
    gate_path = reports / f"cycle-{cycle:02d}-gate.json"
    review_path = reports / f"cycle-{cycle:02d}-review.yaml"
    for path in (gate_path, review_path):
        if not path.is_file():
            raise InputError(f"the acceptance requires {path.name}")
    gate = parse_strict_json(gate_path.read_text(encoding="utf-8"), name="gate result")
    outcome = gate.get("result") or {}
    if gate.get("exit_code") != 0 or outcome.get("outcome") != "approved" or outcome.get("cycle") != cycle:
        raise InputError("only an approved gate for this cycle can produce an acceptance")
    if gate.get("review_sha256") != digest(review_path):
        raise InputError("the recorded gate does not correspond to the current review")
    review = load_data(review_path)
    block = review.get("presentation")
    if not isinstance(block, dict) or block.get("capability") != CAPABILITY:
        raise InputError("the review does not record a supported presentation delivery")
    artifacts = Artifacts(swarm)
    manifest = artifacts.read_json(block["manifest"], "presentation manifest")
    entries = [descriptor(swarm, item["path"]) for item in manifest["files"]]
    for key in ("inputs", "manifest", "inspections"):
        entries.append(descriptor(swarm, block[key]["path"]))
    for name in ("implementation.json", "implementation-evidence.json",
                 "profile.json", "profile-evidence.json",
                 f"cycle-{cycle:02d}-editorial-text.txt", f"cycle-{cycle:02d}-review.yaml",
                 f"cycle-{cycle:02d}-gate.json"):
        entries.append(descriptor(swarm, f"reports/{name}"))
    for item in sorted(reports.glob(f"cycle-{cycle:02d}-reviewer-*.json")):
        entries.append(descriptor(swarm, item.relative_to(swarm).as_posix()))
    entries.append(descriptor(swarm, "brief.md"))
    seen = {item["path"] for item in entries}
    if len(seen) != len(entries):
        raise InputError("the acceptance set repeats a file")
    record = {
        "schema_version": 1, "capability": CAPABILITY, "cycle": cycle,
        "profile": block["profile"],
        "manifest": block["manifest"], "gate": {"path": f"reports/cycle-{cycle:02d}-gate.json",
                                                "sha256": digest(gate_path)},
        "review": {"path": f"reports/cycle-{cycle:02d}-review.yaml", "sha256": digest(review_path)},
        "files": sorted(entries, key=lambda item: item["path"]),
    }
    target = reports / f"cycle-{cycle:02d}-acceptance.json"
    if target.exists():
        raise InputError("an acceptance already exists for this cycle; produce a new candidate instead")
    write_json_atomic(target, record)
    return record


def promote(swarm: Path, cycle: int, destination: Path) -> dict[str, Any]:
    """Copy exactly the accepted set into a new destination directory."""
    swarm = swarm.resolve(strict=True)
    record_path = swarm / "reports" / f"cycle-{cycle:02d}-acceptance.json"
    if not record_path.is_file():
        raise InputError("record the acceptance before copying the delivery")
    record = parse_strict_json(record_path.read_text(encoding="utf-8"), name="acceptance")
    if record.get("capability") != CAPABILITY or record.get("cycle") != cycle:
        raise InputError("the acceptance does not match this cycle")
    destination = destination.resolve()
    if destination.exists():
        raise InputError("the delivery destination must be new; nothing is merged or replaced")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".docswarm-delivery-", dir=destination.parent))
    copied = []
    try:
        for item in [*record["files"], {"path": record_path.relative_to(swarm).as_posix(),
                                        "sha256": digest(record_path), "bytes": record_path.stat().st_size}]:
            source = swarm.joinpath(*logical(item["path"], "accepted file").parts)
            if digest(source) != item["sha256"]:
                raise InputError(f"the accepted file changed before publication: {item['path']}")
            target = staging.joinpath(*logical(item["path"], "accepted file").parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if digest(target) != item["sha256"] or target.stat().st_size != item["bytes"]:
                raise InputError(f"the copy of {item['path']} does not match its recorded bytes")
            copied.append(item["path"])
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"destination": str(destination), "files": copied}
