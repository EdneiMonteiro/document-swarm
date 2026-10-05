"""Stdlib-only verification of recorded PDF inspection artifacts at delivery time."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from scripts.checks.common import InputError


def validate_manifest(manifest) -> None:
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1 or manifest.get("engine") != "docswarm-pdf"
            or manifest.get("engine_version") != "1.0"):
        raise InputError("unsupported PDF manifest")
    if (manifest.get("profile") not in ("textbook", "technical-report")
            or manifest.get("language") not in ("pt-BR", "pt-PT", "en-US", "en-GB", "es-ES")):
        raise InputError("invalid PDF manifest profile or language")
    for name in ("source", "pdf", "layout"):
        item = manifest.get(name)
        if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
            raise InputError(f"PDF manifest requires a valid {name} SHA-256")
    for name, filename in (("pdf", "document.pdf"), ("layout", "layout.json")):
        if manifest[name].get("path") != filename:
            raise InputError(f"PDF manifest must identify its owned {filename}")


def verify_pdf_inspections(review: dict, swarm: Path, brief: dict) -> list[dict[str, str]]:
    deliverables = brief.get("deliverables", [])
    if not isinstance(deliverables, list):
        raise InputError("brief deliverables must be a list")
    expected = {str(item).replace("\\", "/") for item in deliverables
                if isinstance(item, str) and item.lower().endswith(".pdf")}
    versions = [re.match(r"^(\d+)\.(\d+)(?:\.|$)", str(item.get("skill_version", "")))
                for item in (brief, review)]
    required = bool(expected and any(version and tuple(map(int, version.groups())) >= (3, 3) for version in versions))
    if brief.get("pdf_engine") not in (None, "reportlab-v1"):
        raise InputError("unknown PDF composition contract")
    required = required or brief.get("pdf_engine") == "reportlab-v1" or "pdf_inspections" in review
    if not required:
        return []
    records = review.get("pdf_inspections")
    if not isinstance(records, list) or len(records) != len(expected) or not expected:
        raise InputError("each PDF deliverable requires a recorded source, manifest and inspection")
    root = swarm.resolve(strict=True)

    def checked(item, base=root, *, decode=False):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise InputError("PDF artifact reference requires path and sha256")
        name = item["path"].replace("\\", "/")
        logical = PurePosixPath(name)
        if (not name or logical.is_absolute() or PureWindowsPath(name).drive
                or ".." in logical.parts or any(ord(char) < 32 for char in name)):
            raise InputError("unsafe PDF inspection artifact path")
        file = base.joinpath(*logical.parts).resolve(strict=True)
        if root not in file.parents or (base != root and base.resolve() not in file.parents):
            raise InputError("PDF inspection artifact escapes its root")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
            raise InputError("PDF artifact reference requires a SHA-256")
        if not file.is_file() or file.stat().st_size > (16 if decode else 100) * 1024 * 1024:
            raise InputError("PDF inspection artifact is not a bounded file")
        before = file.stat()
        calculated = hashlib.sha256()
        captured = []
        with file.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                calculated.update(chunk)
                if decode:
                    captured.append(chunk)
        after = file.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ino) or calculated.hexdigest() != item["sha256"]):
            raise InputError(f"PDF inspection is stale; artifact changed: {name}")
        return file, json.loads(b"".join(captured).decode("utf-8")) if decode else None

    seen = set()
    blocked = []
    for record in records:
        if not isinstance(record, dict):
            raise InputError("PDF inspection entry must be an object")
        pdf, _ = checked(record.get("pdf"))
        relative = pdf.relative_to(root).as_posix()
        if relative not in expected or relative in seen:
            raise InputError("PDF inspection set differs from the declared deliverables")
        seen.add(relative)
        _, _ = checked(record.get("source"))
        manifest_file, manifest = checked(record.get("manifest"), decode=True)
        _, inspection = checked(record.get("inspection"), decode=True)
        validate_manifest(manifest)
        if (not isinstance(inspection, dict) or type(inspection.get("schema_version")) is not int
                or inspection["schema_version"] != 1 or inspection.get("inspector") != "docswarm-pdf"):
            raise InputError("unsupported PDF inspection report")
        if inspection.get("editorial_approval") != "not_evaluated":
            raise InputError("mechanical PDF inspection must not claim editorial approval")
        recorded_pdf, _ = checked(manifest.get("pdf"), manifest_file.parent)
        if recorded_pdf != pdf:
            raise InputError("PDF manifest points to a different deliverable")
        _, _ = checked(manifest.get("layout"), manifest_file.parent)
        if (inspection.get("pdf_sha256") != record["pdf"]["sha256"]
                or manifest.get("source", {}).get("sha256") != record["source"]["sha256"]
                or inspection.get("source_sha256") != record["source"]["sha256"]
                or inspection.get("manifest_sha256") != record["manifest"]["sha256"]
                or inspection.get("layout_sha256") != manifest["layout"]["sha256"]):
            raise InputError("PDF inspection hashes do not match source and delivery artifacts")
        pages = inspection.get("pages")
        previews = inspection.get("previews")
        count = inspection.get("page_count")
        if (type(count) is not int or not 1 <= count <= 300 or not isinstance(pages, list) or len(pages) != count
                or not isinstance(previews, list) or len(previews) != count):
            raise InputError("PDF inspection requires every actual page and PNG preview")
        if (not all(isinstance(item, dict) and type(item.get("page")) is int for item in pages)
                or {item["page"] for item in pages} != set(range(1, count + 1))):
            raise InputError("PDF page inventory is incomplete")
        if (not all(isinstance(item, dict) and type(item.get("page")) is int for item in previews)
                or {item["page"] for item in previews} != set(range(1, count + 1))):
            raise InputError("PDF previews are incomplete")
        for preview in previews:
            if preview.get("path") != f"previews/page-{preview['page']:03d}.png":
                raise InputError("PDF previews must identify distinct owned page images")
            checked(preview, manifest_file.parent)
        checked(inspection.get("editorial_text"), manifest_file.parent)
        errors = inspection.get("errors")
        if (not isinstance(errors, list) or inspection.get("status") not in ("pass", "fail")
                or not all(isinstance(item, dict) and isinstance(item.get("code"), str) and item["code"] for item in errors)):
            raise InputError("invalid PDF inspection outcome")
        if (inspection["status"] == "pass") != (len(errors) == 0):
            raise InputError("PDF inspection status contradicts its errors")
        if errors:
            blocked.append({"kind": "pdf", "name": relative, "grade": "", "reviewer": "mechanical inspection"})
    return blocked
