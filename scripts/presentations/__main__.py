"""Run the presentation engine: preflight, build, inspect and publish.

Importing this module does not pull in a browser, PowerPoint or python-pptx:
the dependencies are loaded only by the operation that needs them, and a missing
one is reported explicitly instead of producing an empty result.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, write_json_atomic

CAPABILITY = "presentation-v1"
INSPECTOR = "docswarm-presentation"
INSPECTOR_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[2]
SELECTORS = {
    "compatibility": "tests.test_presentation_contract.CompatibilityTests",
    "adversarial-detectors": "tests.test_presentation_engine.DetectorTests",
    "lifecycle": "tests.test_presentation_engine.LifecycleTests",
}
VISUAL_CEILING = 0.12


def run_selector(selector: str) -> dict[str, Any]:
    """Execute one test selector and record what it actually reported."""
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(selector)
    except Exception as exc:  # pragma: no cover - surfaced in the record
        return {"status": "not_evaluated", "findings": [{"code": "selector_unavailable", "detail": str(exc)[:300]}],
                "observations": {"selector": selector}}
    runner = unittest.TextTestRunner(stream=open(Path(tempfile.gettempdir()) / "docswarm-selector.log", "w",
                                                 encoding="utf-8"), verbosity=0)
    result = runner.run(suite)
    findings = [{"code": "test_failure", "test": str(case)} for case, _ in result.failures + result.errors]
    return {"status": "pass" if result.wasSuccessful() and result.testsRun else "fail" if findings else "not_evaluated",
            "findings": findings,
            "observations": {"selector": selector, "tests": result.testsRun, "skipped": len(result.skipped)}}


def qualify_profile(profile: str, isolation: Path | None, workspace: Path) -> list[dict[str, Any]]:
    """Qualify the environment on synthetic material, never on the candidate."""
    from scripts.presentations import capture, inspect, office, pptx_editable, pptx_faithful
    from scripts.presentations.html import write_package
    from scripts.presentations.layout import plan
    from scripts.presentations.model import read_deck

    document = read_deck(ROOT / "tests" / "fixtures" / "presentations" / "reference-deck.json")
    layout = plan(document, profile)
    package = workspace / "package"
    write_package(document, layout, package)
    frames = capture.capture(package / "index.html", layout, workspace / "frames")
    editable = workspace / "deck-editable.pptx"
    faithful = workspace / "deck-faithful.pptx"
    pptx_editable.export(document, layout, editable)
    pptx_faithful.export(document, layout, frames, faithful)

    capability: dict[str, Any] = {"check_id": "component-capability", "findings": [], "observations": {}}
    visual: dict[str, Any] = {"check_id": "visual-font-calibration", "findings": [], "observations": {}}
    isolation_check: dict[str, Any] = {"check_id": "interactive-isolation", "findings": [], "observations": {}}
    try:
        rehearsal = office.rehearse(editable, faithful, layout, workspace / "office", isolation=isolation)
    except office.Unavailable as exc:
        detail = {"code": "office_unavailable", "detail": str(exc)}
        for record in (capability, visual, isolation_check):
            record["status"] = "not_evaluated"
            record["findings"] = [detail]
        return [capability, visual, isolation_check]

    capability["findings"] = [item for item in rehearsal["findings"] if item["code"] != "office_export_size"]
    capability["observations"] = {"editing": rehearsal["editing"],
                                  "actions": len(rehearsal.get("faithful_actions", []))}
    capability["status"] = "pass" if not capability["findings"] else "fail"

    differences = []
    for index, frame in enumerate(rehearsal.get("faithful_frames", [])):
        comparison = inspect.compare_images(Path(frames[index]["path"]), Path(frame["path"]),
                                            tolerance=VISUAL_CEILING)
        differences.append({"slide": frame["slide"], **comparison})
    worst = max((item["changed_ratio"] for item in differences), default=1.0)
    visual["observations"] = {"ceiling": VISUAL_CEILING, "worst_changed_ratio": worst,
                              "pages": differences, "fonts": layout["fonts"]}
    visual["findings"] = [item for item in differences if not item["within_tolerance"]]
    visual["status"] = "pass" if differences and not visual["findings"] else "fail" if differences else "not_evaluated"

    record = rehearsal["isolation"]
    isolation_check["observations"] = {key: value for key, value in record.items() if key != "status"}
    isolation_check["status"] = record["status"]
    isolation_check["findings"] = ([] if record["status"] == "pass"
                                   else [{"code": "isolation_not_demonstrated", "reason": record.get("reason", "")}])
    return [capability, visual, isolation_check]


def preflight(args: argparse.Namespace) -> int:
    from scripts.presentations.profile import write_records

    swarm = args.swarm.resolve(strict=True)
    implementation = [{"check_id": name, **run_selector(selector)} for name, selector in SELECTORS.items()]
    workspace = Path(tempfile.mkdtemp(prefix="docswarm-qualify-"))
    try:
        profile_checks = qualify_profile(args.profile, args.isolation, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    subjects = write_records(swarm / "reports", implementation_checks=implementation,
                             profile_checks=profile_checks, profile=args.profile)
    summary = {"profile": args.profile, "subjects": subjects,
               "implementation": {item["check_id"]: item["status"] for item in implementation},
               "qualification": {item["check_id"]: item["status"] for item in profile_checks}}
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if all(item["status"] == "pass" for item in implementation + profile_checks) else 1


def assemble(swarm: Path, cycle: int, profile: str, result: dict[str, Any],
             implementation: list[dict[str, Any]], profile_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the inputs, manifest, inspections and editorial text of one cycle."""
    from scripts.presentations.build import editorial_text
    from scripts.presentations.model import digest

    reports = swarm / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for name in ("implementation.json", "profile.json"):
        if not (reports / name).is_file():
            raise InputError(f"run the preflight first: reports/{name} is missing")
    inputs = {
        "schema_version": 1, "capability": CAPABILITY, "cycle": cycle, "profile": profile,
        "implementation": {"path": "reports/implementation.json", "sha256": digest(reports / "implementation.json")},
        "profile_record": {"path": "reports/profile.json", "sha256": digest(reports / "profile.json")},
        "files": [item for item in result["manifest"]["files"]
                  if item["role"] in ("model", "theme", "asset", "font", "license", "runtime")],
        "external_provenance": [],
    }
    inputs["profile"] = inputs.pop("profile_record")
    inputs["profile_name"] = profile
    inputs_path = reports / f"cycle-{cycle:02d}-inputs.json"
    write_json_atomic(inputs_path, inputs)
    manifest = {**result["manifest"], "inputs": {"path": inputs_path.relative_to(swarm).as_posix(),
                                                 "sha256": digest(inputs_path)}}
    manifest_path = reports / f"cycle-{cycle:02d}-presentation-manifest.json"
    write_json_atomic(manifest_path, manifest)
    text_path = reports / f"cycle-{cycle:02d}-editorial-text.txt"
    text_path.write_text(editorial_text(result["layout"]), encoding="utf-8")

    manifest_digest = digest(manifest_path)
    reports_list = []
    for item in implementation:
        reports_list.append({"scope": "implementation", "subject_sha256": digest(reports / "implementation.json"),
                             "inspector_version": INSPECTOR_VERSION, **item})
    for item in profile_checks:
        reports_list.append({"scope": "profile", "subject_sha256": digest(reports / "profile.json"),
                             "inspector_version": INSPECTOR_VERSION, **item})
    for item in result["checks"]:
        findings = item.get("findings", [])
        reports_list.append({"scope": "candidate", "cycle": cycle, "subject_sha256": manifest_digest,
                             "inspector_version": INSPECTOR_VERSION,
                             "status": "pass" if not findings else "fail", **item})
    edit_findings = [finding for item in profile_checks if item["check_id"] == "component-capability"
                     for finding in item.get("findings", []) if finding.get("code") == "edit_not_persisted"]
    reports_list.append({"scope": "candidate", "check_id": "edit-save-reopen", "cycle": cycle,
                         "subject_sha256": manifest_digest, "inspector_version": INSPECTOR_VERSION,
                         "status": next((item["status"] for item in profile_checks
                                         if item["check_id"] == "component-capability"), "not_evaluated"),
                         "findings": edit_findings,
                         "observations": {"source": "profile component-capability rehearsal"}})
    inspections = {"schema_version": 1, "inspector": INSPECTOR, "capability": CAPABILITY,
                   "cycle": cycle, "profile": profile, "reports": reports_list}
    inspections_path = reports / f"cycle-{cycle:02d}-presentation-inspections.json"
    write_json_atomic(inspections_path, inspections)
    return {
        "inputs": {"path": inputs_path.relative_to(swarm).as_posix(), "sha256": digest(inputs_path)},
        "manifest": {"path": manifest_path.relative_to(swarm).as_posix(), "sha256": manifest_digest},
        "inspections": {"path": inspections_path.relative_to(swarm).as_posix(), "sha256": digest(inspections_path)},
        "editorial_text": {"path": text_path.relative_to(swarm).as_posix(), "sha256": digest(text_path)},
        "status": "pass" if all(item["status"] == "pass" for item in reports_list) else "fail",
        "reports": {f"{item['scope']}/{item['check_id']}": item["status"] for item in reports_list},
    }


def build_command(args: argparse.Namespace) -> int:
    from scripts.presentations.build import build
    from scripts.presentations.profile import digest_file

    swarm = args.swarm.resolve(strict=True)
    reports = swarm / "reports"
    implementation = json.loads((reports / "implementation-evidence.json").read_text(encoding="utf-8"))["checks"]
    profile_checks = json.loads((reports / "profile-evidence.json").read_text(encoding="utf-8"))["checks"]
    result = build(args.deck, swarm, args.destination, profile=args.profile, cycle=args.cycle,
                   assets_root=args.assets)
    summary = assemble(swarm, args.cycle, args.profile, result, implementation, profile_checks)
    summary["destination"] = str(result["destination"])
    summary["pages"] = len(result["layout"]["pages"])
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if summary["status"] == "pass" else 1


def inspect_command(args: argparse.Namespace) -> int:
    from scripts.presentations import capture, inspect as inspector
    from scripts.presentations.layout import plan
    from scripts.presentations.model import read_deck

    destination = args.destination.resolve(strict=True)
    document = read_deck(destination / "deck.json", assets_root=destination / "assets")
    layout = json.loads((destination / "layout.json").read_text(encoding="utf-8"))
    if layout.get("deck_sha256") != document["sha256"]:
        raise InputError("the delivered plan was derived from another deck; rebuild the candidate")
    recomputed = plan(document, layout["profile"])
    if [page["page_id"] for page in recomputed["pages"]] != [page["page_id"] for page in layout["pages"]]:
        raise InputError("recomposing the deck produces a different page inventory")
    checks = [
        {"check_id": "html-offline", **inspector.inspect_html(destination / "index.html", layout)},
        {"check_id": "navigation", **capture.exercise(destination / "index.html", layout)},
        {"check_id": "native-structure",
         **inspector.inspect_pptx(destination / "deck-editable.pptx", layout, editable=True)},
        {"check_id": "visual", **inspector.inspect_pptx(destination / "deck-faithful.pptx", layout, editable=False)},
    ]
    summary = {"destination": str(destination),
               "status": "pass" if all(not item["findings"] for item in checks) else "fail",
               "checks": {item["check_id"]: len(item["findings"]) for item in checks},
               "findings": [finding for item in checks for finding in item["findings"]][:20]}
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if summary["status"] == "pass" else 1


def publish_command(args: argparse.Namespace) -> int:
    from scripts.presentations.publish import acceptance, promote

    swarm = args.swarm.resolve(strict=True)
    record = acceptance(swarm, args.cycle)
    result = promote(swarm, args.cycle, args.destination)
    print(json.dumps({"files": len(record["files"]), **result}, ensure_ascii=True, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose, inspect and publish a presentation delivery.")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    pre = subparsers.add_parser("preflight", help="qualify the implementation and the environment profile")
    pre.add_argument("--swarm", type=Path, required=True)
    pre.add_argument("--profile", default="windows-powerpoint-v1")
    pre.add_argument("--isolation", type=Path, help="operator evidence that the station had no outbound network")

    make = subparsers.add_parser("build", help="compose and inspect a new candidate")
    make.add_argument("--swarm", type=Path, required=True)
    make.add_argument("--deck", type=Path, required=True)
    make.add_argument("--destination", type=Path, required=True, help="new directory under the swarm output")
    make.add_argument("--profile", default="windows-powerpoint-v1")
    make.add_argument("--cycle", type=int, required=True)
    make.add_argument("--assets", type=Path, help="authorised asset root; defaults to the deck directory")

    look = subparsers.add_parser("inspect", help="re-inspect an existing candidate without changing it")
    look.add_argument("--destination", type=Path, required=True)

    out = subparsers.add_parser("publish", help="record the acceptance and copy the accepted set")
    out.add_argument("--swarm", type=Path, required=True)
    out.add_argument("--cycle", type=int, required=True)
    out.add_argument("--destination", type=Path, required=True)

    args = parser.parse_args(argv)
    handlers = {"preflight": preflight, "build": build_command,
                "inspect": inspect_command, "publish": publish_command}
    try:
        return handlers[args.operation](args)
    except ModuleNotFoundError as exc:
        print(f"Presentation dependencies unavailable ({exc.name}). Install requirements-presentations.txt; "
              "the deterministic document checks do not require them.", file=sys.stderr)
        return 2
    except (InputError, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: presentation {args.operation} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
