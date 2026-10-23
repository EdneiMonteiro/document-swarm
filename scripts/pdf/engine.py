"""Public render/inspect interface with explicit artifact ownership and hashes."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from scripts.checks.common import InputError, write_json_atomic
from scripts.checks.pdf_contract import validate_manifest
from scripts.pdf.inspect import digest, inspect_pdf
from scripts.pdf.render import compose
from scripts.pdf.source import read_source


def component_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or value not in {"document.pdf", "layout.json"}:
        raise InputError("invalid owned PDF artifact name")
    path = root / value
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise InputError(f"PDF artifact escapes its destination: {value}")
    return path


def render(source: Path, destination: Path, profile: str = "textbook", language: str = "pt-BR") -> dict:
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".md":
        raise InputError("PDF composition expects an authored Markdown source")
    if destination.exists():
        raise InputError("render destination must be new; use a new cycle directory to preserve reviewed artifacts")
    if not destination.name or destination.is_symlink():
        raise InputError("invalid PDF destination directory")
    document = read_source(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".docswarm-pdf-", dir=destination.parent))
    committed = False
    try:
        pdf = temporary / "document.pdf"
        layout = compose(document, pdf, profile, language)
        write_json_atomic(temporary / "layout.json", layout)
        manifest = {
            "schema_version": 1, "engine": "docswarm-pdf", "engine_version": "1.0",
            "source": {"name": document.source_name, "sha256": document.source_sha256},
            "pdf": {"path": "document.pdf", "sha256": digest(pdf)},
            "layout": {"path": "layout.json", "sha256": digest(temporary / "layout.json")},
            "profile": profile, "language": language,
        }
        write_json_atomic(temporary / "manifest.json", manifest)
        report = inspect(source, temporary)
        if digest(source) != document.source_sha256:
            raise InputError("source changed during PDF generation")
        # The owned bundle is published only after composition and inspection finish.
        if destination.exists():
            raise InputError("destination appeared while rendering; refusing to replace it")
        os.rename(temporary, destination)
        committed = True
        return {
            "destination": str(destination), "pdf": str(destination / "document.pdf"),
            "manifest": str(destination / "manifest.json"), "inspection": str(destination / "inspection.json"),
            "previews": str(destination / "previews"), "status": report["status"],
            "page_count": report["page_count"], "errors": report["errors"],
            "editorial_approval": "not_evaluated",
            "source_sha256": document.source_sha256, "pdf_sha256": report["pdf_sha256"],
            "manifest_sha256": digest(destination / "manifest.json"),
            "inspection_sha256": digest(destination / "inspection.json"),
        }
    finally:
        if not committed and temporary.exists():
            shutil.rmtree(temporary)


def inspect(source: Path, destination: Path, profile: str | None = None, language: str | None = None) -> dict:
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".md":
        raise InputError("PDF inspection expects an authored Markdown source")
    root = destination.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 1024 * 1024:
        raise InputError("unsafe PDF manifest")
    manifest_hash = digest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    if profile is not None and profile != manifest.get("profile"):
        raise InputError("requested inspection profile differs from the rendered bundle")
    if language is not None and language != manifest.get("language"):
        raise InputError("requested inspection language differs from the rendered bundle")
    pdf = component_path(root, manifest["pdf"].get("path"))
    layout_path = component_path(root, manifest["layout"].get("path"))
    if layout_path.stat().st_size > 16 * 1024 * 1024:
        raise InputError("layout exceeds size limit")
    if manifest["layout"]["sha256"] != digest(layout_path):
        raise InputError("layout manifest was altered; re-render instead of trusting modified geometry")
    for name in ("inspection.json", "editorial-text.txt", "previews"):
        path = root / name
        if path.is_symlink() or (path.exists() and root not in path.resolve().parents):
            raise InputError(f"unsafe inspection destination: {name}")
    previews = root / "previews"
    previous_previews = []
    if previews.exists():
        for child in previews.iterdir():
            if child.is_symlink() or not child.is_file() or not re.fullmatch(r"page-\d{3}\.png", child.name):
                raise InputError("previews must contain only owned PNG files")
            previous_previews.append(child)
    document = read_source(source)
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    if (not isinstance(layout, dict) or layout.get("profile") != manifest["profile"]
            or layout.get("language") != manifest["language"]):
        raise InputError("layout profile/language do not match the manifest")
    with tempfile.TemporaryDirectory(prefix=".docswarm-inspect-", dir=root) as temporary:
        staging = Path(temporary)
        report = inspect_pdf(document, pdf, layout, staging / "previews")
        if document.source_sha256 != manifest["source"]["sha256"]:
            report["errors"].append({"code": "source_changed_since_render"})
        if report["pdf_sha256"] != manifest["pdf"]["sha256"]:
            report["errors"].append({"code": "pdf_changed_since_render"})
        if (digest(source) != document.source_sha256 or digest(manifest_path) != manifest_hash
                or digest(layout_path) != manifest["layout"]["sha256"]):
            raise InputError("source or bundle metadata changed during inspection")
        text_path = staging / "editorial-text.txt"
        text_path.write_text(report.pop("extracted_text"), encoding="utf-8")
        report["editorial_text"] = {"path": "editorial-text.txt", "sha256": digest(text_path)}
        report["manifest_sha256"] = manifest_hash
        report["layout_sha256"] = manifest["layout"]["sha256"]
        report["status"] = "fail" if report["errors"] else "pass"
        previews.mkdir(exist_ok=True)
        current_previews = {Path(item["path"]).name for item in report["previews"]}
        for name in sorted(current_previews):
            os.replace(staging / "previews" / name, previews / name)
        for previous in previous_previews:
            if previous.name not in current_previews:
                previous.unlink()
        os.replace(text_path, root / "editorial-text.txt")
        write_json_atomic(root / "inspection.json", report)
    return report
