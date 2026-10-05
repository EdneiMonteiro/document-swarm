from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.checks import gate
from scripts.checks.common import InputError
from scripts.checks.presentation_contract import CAPABILITY, FORMATS, verify_presentation

TOPIC = "T01"
PAGES = ("sys:index:1", "alpha", "sys:support:alpha:beta:1")
NAVIGATION = [
    ("sys:index:1", "sys:nav:previous", None), ("sys:index:1", "sys:nav:next", "alpha"),
    ("sys:index:1", "sys:index:entry:alpha", "alpha"),
    ("alpha", "sys:nav:previous", "sys:index:1"), ("alpha", "sys:nav:next", None),
    ("alpha", "sys:nav:index", "sys:index:1"), ("alpha", "abrir", "sys:support:alpha:beta:1"),
    ("sys:support:alpha:beta:1", "sys:nav:previous", None),
    ("sys:support:alpha:beta:1", "sys:nav:next", None),
    ("sys:support:alpha:beta:1", "sys:nav:back", "alpha"),
]


class Delivery:
    """Assemble a synthetic but structurally complete presentation record."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.output = root / "output" / "presentation-cycle-01"
        self.reports = root / "reports"
        self.output.mkdir(parents=True)
        self.reports.mkdir(parents=True)
        (root / "brief.md").write_text(
            '---\nswarm_id: presentation-fixture\nskill_version: "3.4.0"\nmode: document\n'
            "artifact_type: presentation\nmax_cycles: 3\nquality_contract: editorial-v1\n"
            "editorial_reviewer: reviewer-01-clarity\n"
            "presentation:\n  schema_version: 1\n  capability: presentation-v1\n"
            "  deck_path: output/presentation-cycle-01/deck.json\n  profile: windows-powerpoint-v1\n"
            "  required_formats:\n    - html-offline\n    - pptx-faithful\n    - pptx-editable\n"
            "deliverables:\n  - output/presentation-cycle-01/index.html\n"
            "  - output/presentation-cycle-01/deck-faithful.pptx\n"
            "  - output/presentation-cycle-01/deck-editable.pptx\n---\n# Synthetic presentation fixture\n",
            encoding="utf-8")
        self.deck = {
            "schema_version": 1, "deck_id": "fixture", "language": "pt-BR", "title": "Fixture",
            "size_pt": {"width": 960, "height": 540}, "theme_ref": "neutral-v1",
            "index": {"title": "Sumário"}, "topics": [TOPIC],
            "actions": [{"action_id": "abrir", "trigger_block_id": "controle",
                         "kind": "open_support", "support_id": "beta"}],
            "slides": [{"slide_id": "alpha", "topic_ids": [TOPIC], "layout": "title-and-body",
                        "title": [{"text": "Alpha"}], "presenter_notes": "Notas de alpha.",
                        "blocks": [{"block_id": "controle", "type": "control",
                                    "layout": {"area": "body"},
                                    "data": {"action_id": "abrir", "label": "Abrir"}}]}],
            "supports": [{"support_id": "beta", "layout": "title-and-body", "title": [{"text": "Beta"}],
                          "presenter_notes": "Notas de beta.",
                          "blocks": [{"block_id": "detalhe", "type": "text", "layout": {"area": "body"},
                                      "data": {"paragraphs": [{"runs": [{"text": "Detalhe"}]}]}}]}],
        }
        self.layout = {
            "schema_version": 1, "profile": "windows-powerpoint-v1", "deck_sha256": "",
            "size_pt": {"width": 960, "height": 540},
            "pages": [
                {"page_id": "sys:index:1", "kind": "index", "entries": ["alpha"], "blocks": [],
                 "notes_source": None},
                {"page_id": "alpha", "kind": "slide", "entries": [], "blocks": ["controle"],
                 "notes_source": "alpha"},
                {"page_id": "sys:support:alpha:beta:1", "kind": "support", "entries": [],
                 "blocks": ["detalhe"], "notes_source": "beta"},
            ],
            "navigation": [{"page_id": page, "action_id": action, "enabled": target is not None,
                            "target_page_id": target, "name": action}
                           for page, action, target in NAVIGATION],
        }
        self.write()

    # -- files -------------------------------------------------------------
    def write(self) -> None:
        self.blob(self.output / "deck.json", json.dumps(self.deck))
        self.layout["deck_sha256"] = self.sha(self.output / "deck.json")
        self.blob(self.output / "layout.json", json.dumps(self.layout))
        self.blob(self.output / "index.html", "<!DOCTYPE html><html><body>fixture</body></html>")
        for name in ("deck-faithful.pptx", "deck-editable.pptx"):
            self.blob(self.output / name, f"PK-fixture-{name}")
        self.blob(self.reports / "implementation.json", json.dumps({"schema_version": 1}))
        self.blob(self.reports / "profile.json", json.dumps({"schema_version": 1}))
        self.refresh()

    @staticmethod
    def blob(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def sha(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def ref(self, path: Path) -> dict[str, str]:
        return {"path": path.relative_to(self.root).as_posix(), "sha256": self.sha(path)}

    def inventory(self) -> list[dict[str, object]]:
        roles = {"deck.json": "model", "layout.json": "plan", "index.html": "format",
                 "deck-faithful.pptx": "format", "deck-editable.pptx": "format"}
        files = []
        for item in sorted(self.output.rglob("*")):
            if item.is_file():
                files.append({**self.ref(item), "role": roles.get(item.name, "runtime"),
                              "bytes": item.stat().st_size})
        return files

    def refresh(self) -> None:
        """Rebuild the manifest, inspections and review around the current files."""
        self.inputs = {"schema_version": 1, "capability": CAPABILITY, "cycle": 1,
                       "implementation": self.ref(self.reports / "implementation.json"),
                       "profile": self.ref(self.reports / "profile.json"), "files": [],
                       "external_provenance": []}
        self.blob(self.reports / "cycle-01-inputs.json", json.dumps(self.inputs))
        self.manifest = {
            "schema_version": 1, "capability": CAPABILITY, "cycle": 1, "profile": "windows-powerpoint-v1",
            "generator": "docswarm-presentation", "generator_version": "1.0",
            "inputs": self.ref(self.reports / "cycle-01-inputs.json"),
            "deck": self.ref(self.output / "deck.json"), "layout": self.ref(self.output / "layout.json"),
            "formats": {"html-offline": self.ref(self.output / "index.html"),
                        "pptx-faithful": self.ref(self.output / "deck-faithful.pptx"),
                        "pptx-editable": self.ref(self.output / "deck-editable.pptx")},
            "files": self.inventory(),
            "pages": [{"page_id": page, "kind": kind} for page, kind
                      in zip(PAGES, ("index", "slide", "support"))],
        }
        self.blob(self.reports / "cycle-01-presentation-manifest.json", json.dumps(self.manifest))
        manifest_sha = self.sha(self.reports / "cycle-01-presentation-manifest.json")
        reports = []
        for scope, checks, subject in (
            ("implementation", ("compatibility", "adversarial-detectors", "lifecycle"),
             self.sha(self.reports / "implementation.json")),
            ("profile", ("component-capability", "visual-font-calibration", "interactive-isolation"),
             self.sha(self.reports / "profile.json")),
            ("candidate", ("input-policy", "inventory-content", "html-offline", "navigation",
                           "visual", "native-structure", "edit-save-reopen"), manifest_sha),
        ):
            for check in checks:
                entry = {"scope": scope, "check_id": check, "subject_sha256": subject,
                         "inspector_version": "1.0", "status": "pass", "findings": [],
                         "observations": {}}
                if scope == "candidate":
                    entry["cycle"] = 1
                reports.append(entry)
        self.inspections = {"schema_version": 1, "inspector": "docswarm-presentation",
                            "capability": CAPABILITY, "cycle": 1, "profile": "windows-powerpoint-v1",
                            "reports": reports}
        self.blob(self.reports / "cycle-01-presentation-inspections.json", json.dumps(self.inspections))
        self.rows = self.full_coverage()
        self.save_review()

    def full_coverage(self) -> list[dict[str, object]]:
        rows = []
        for dimension in ("factual", "decision"):
            rows.append({"reviewer": "reviewer-02-content", "dimension": dimension, "topic_id": TOPIC,
                         "grade": "A", "justification": "Annotated fixture judgment.", "action": ""})
        for dimension in ("legibility", "interaction"):
            for fmt in FORMATS:
                for page in PAGES:
                    rows.append({"reviewer": "reviewer-03-visual", "dimension": dimension, "format": fmt,
                                 "page_id": page, "grade": "A",
                                 "justification": "Annotated fixture judgment.", "action": ""})
        for page in PAGES:
            rows.append({"reviewer": "reviewer-03-visual", "dimension": "editability",
                         "format": "pptx-editable", "page_id": page, "grade": "A",
                         "justification": "Annotated fixture judgment.", "action": ""})
        return rows

    def block(self) -> dict[str, object]:
        return {"schema_version": 1, "capability": CAPABILITY, "cycle": 1,
                "profile": "windows-powerpoint-v1",
                "inputs": self.ref(self.reports / "cycle-01-inputs.json"),
                "manifest": self.ref(self.reports / "cycle-01-presentation-manifest.json"),
                "inspections": self.ref(self.reports / "cycle-01-presentation-inspections.json"),
                "reviews": self.rows}

    def save_review(self) -> None:
        by_reviewer: dict[str, list[dict[str, object]]] = {}
        for row in self.rows:
            by_reviewer.setdefault(str(row["reviewer"]), []).append(row)
        for reviewer, rows in by_reviewer.items():
            self.blob(self.reports / f"cycle-01-{reviewer}.json",
                      json.dumps({"schema_version": 1, "cycle": 1, "reviewer": reviewer,
                                  "presentation_reviews": rows}))

    def review(self) -> dict[str, object]:
        return {"schema_version": 1, "skill_version": "3.4.0", "cycle": 1, "max_cycles": 3,
                "topics": [{"topico": TOPIC, "nota_minima": "A",
                            "revisor_da_minima": "reviewer-02-content", "bloqueia": False}],
                "rubberduck": {"critico": False, "achados": []},
                "presentation": self.block()}

    def brief(self) -> dict[str, object]:
        from scripts.checks.lint_agents import frontmatter_text
        return frontmatter_text((self.root / "brief.md").read_text(encoding="utf-8"))

    def verify(self, review=None):
        return verify_presentation(review or self.review(), self.root, self.brief())


class PresentationFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.delivery = Delivery(Path(self.temporary.name) / "swarm")


class CompatibilityTests(PresentationFixture):
    """The contract approves a complete record and nothing less."""

    def test_complete_record_passes_and_binds_every_artifact(self):
        self.assertEqual(self.delivery.verify(), [])
        recorded = self.delivery.review()
        for name in ("output/presentation-cycle-01/deck.json",
                     "output/presentation-cycle-01/index.html",
                     "output/presentation-cycle-01/deck-editable.pptx",
                     "reports/cycle-01-presentation-manifest.json",
                     "reports/cycle-01-presentation-inspections.json"):
            target = self.delivery.root / name
            original = target.read_bytes()
            with self.subTest(changed=name):
                target.write_bytes(original + b" ")
                with self.assertRaises(InputError):
                    self.delivery.verify(recorded)
                target.write_bytes(original)
        self.assertEqual(self.delivery.verify(recorded), [])

    def test_document_deliveries_are_untouched_by_the_presentation_contract(self):
        self.assertEqual(verify_presentation({}, self.delivery.root, {}), [])
        self.assertEqual(verify_presentation({}, self.delivery.root, {"artifact_type": "document"}), [])
        with self.assertRaises(InputError):
            verify_presentation({"presentation": {}}, self.delivery.root, {"artifact_type": "document"})

    def test_unsupported_capability_or_formats_are_refused(self):
        brief = self.delivery.brief()
        for change in ("capability", "formats", "profile", "artifact_type"):
            data = copy.deepcopy(brief)
            with self.subTest(change=change):
                if change == "capability":
                    data["presentation"]["capability"] = "presentation-v2"
                elif change == "formats":
                    data["presentation"]["required_formats"] = ["html-offline"]
                elif change == "profile":
                    data["presentation"]["profile"] = "another-profile"
                else:
                    data["artifact_type"] = "deck"
                with self.assertRaises(InputError):
                    verify_presentation(self.delivery.review(), self.delivery.root, data)

    def test_legacy_slide_fields_cannot_be_combined_with_the_contract(self):
        review = self.delivery.review()
        review["slides"] = [{"slide": "S1", "nota_minima": "A"}]
        with self.assertRaises(InputError):
            self.delivery.verify(review)

    def test_gate_returns_three_for_an_incomplete_record_and_one_for_a_low_grade(self):
        path = self.delivery.reports / "cycle-01-review.yaml"
        output = self.delivery.reports / "cycle-01-gate.json"

        def run(review):
            path.write_text(json.dumps(review), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return gate.main([str(path), "--output", str(output)])

        review = self.delivery.review()
        review["editorial"] = self.editorial()
        self.assertEqual(run(review), 0)
        low = copy.deepcopy(review)
        low["presentation"]["reviews"][0]["grade"] = "B+"
        low["presentation"]["reviews"][0]["action"] = "Corrigir a afirmação do tópico."
        self.delivery.rows = low["presentation"]["reviews"]
        self.delivery.save_review()
        self.assertEqual(run(low), 1)
        broken = copy.deepcopy(review)
        broken["presentation"]["inspections"]["sha256"] = "0" * 64
        self.assertEqual(run(broken), 3)

    def editorial(self):
        text = self.delivery.reports / "cycle-01-editorial-text.txt"
        body = "Alpha\nAbrir\nBeta\nDetalhe\nNotas de alpha.\n"
        text.write_text(body, encoding="utf-8")
        quotes = ["Alpha", "Abrir", "Detalhe", "Beta", "Notas de alpha."]
        editorial = {
            "schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity", "scope": "full_document",
            "text": self.delivery.ref(text),
            "artifacts": [self.delivery.ref(self.delivery.output / name)
                          for name in ("index.html", "deck-faithful.pptx", "deck-editable.pptx")],
            "surfaces": [{"surface": surface, "grade": "A", "location": surface, "quote": quote,
                          "justification": "Annotated fixture judgment about the delivered wording.",
                          "action": ""}
                         for surface, quote in zip(gate.EDITORIAL_SURFACES, quotes)],
            "findings": [],
        }
        self.delivery.blob(self.delivery.reports / "cycle-01-reviewer-01-clarity.json",
                           json.dumps({"schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity",
                                       "editorial": editorial}))
        return editorial


class CoverageTests(PresentationFixture):
    """The expected domain is rebuilt from the deck, not read from the manifest."""

    def test_every_dimension_page_and_format_must_be_graded_once(self):
        for change in ("missing", "duplicate", "unknown-page", "wrong-dimension", "editability-skipped"):
            delivery = self.delivery
            rows = copy.deepcopy(delivery.full_coverage())
            with self.subTest(change=change):
                if change == "missing":
                    rows.pop()
                elif change == "duplicate":
                    rows.append(copy.deepcopy(rows[0]))
                elif change == "unknown-page":
                    rows[-1]["page_id"] = "sys:support:alpha:beta:9"
                elif change == "wrong-dimension":
                    rows[-1]["dimension"] = "not_applicable"
                else:
                    rows = [row for row in rows if row["dimension"] != "editability"]
                delivery.rows = rows
                delivery.save_review()
                with self.assertRaises(InputError):
                    delivery.verify()

    def test_a_hidden_support_page_cannot_shrink_the_domain(self):
        delivery = self.delivery
        delivery.layout["pages"] = [page for page in delivery.layout["pages"]
                                    if page["kind"] != "support"]
        delivery.layout["navigation"] = [item for item in delivery.layout["navigation"]
                                         if not item["page_id"].startswith("sys:support:")]
        delivery.write()
        with self.assertRaisesRegex(InputError, "support|materialis"):
            delivery.verify()

    def test_the_planned_navigation_must_equal_the_reconstructed_graph(self):
        for change in ("drop", "retarget", "enable-extreme"):
            delivery = Delivery(Path(self.temporary.name) / f"swarm-{change}")
            with self.subTest(change=change):
                if change == "drop":
                    delivery.layout["navigation"].pop()
                elif change == "retarget":
                    delivery.layout["navigation"][1]["target_page_id"] = "sys:index:1"
                else:
                    delivery.layout["navigation"][0]["enabled"] = True
                    delivery.layout["navigation"][0]["target_page_id"] = "alpha"
                delivery.write()
                with self.assertRaises(InputError):
                    delivery.verify()

    def test_an_individual_reviewer_report_must_match_the_consolidated_grades(self):
        delivery = self.delivery
        rows = copy.deepcopy(delivery.rows)
        rows[0]["grade"] = "A+"
        review = delivery.review()
        review["presentation"]["reviews"] = rows
        with self.assertRaisesRegex(InputError, "differ from the report"):
            delivery.verify(review)


class EvidenceTests(PresentationFixture):
    """Inspections must be executed, current and bound to their own subject."""

    def set_status(self, check: str, status: str, findings=None):
        for entry in self.delivery.inspections["reports"]:
            if entry["check_id"] == check:
                entry["status"] = status
                entry["findings"] = findings or []
        self.delivery.blob(self.delivery.reports / "cycle-01-presentation-inspections.json",
                           json.dumps(self.delivery.inspections))
        block = self.delivery.block()
        review = self.delivery.review()
        review["presentation"] = block
        return review

    def test_a_pending_or_unsupported_check_never_approves(self):
        for status in ("pending", "not_evaluated", "unsupported", "stale"):
            with self.subTest(status=status):
                review = self.set_status("interactive-isolation", status)
                with self.assertRaisesRegex(InputError, "never approves"):
                    self.delivery.verify(review)

    def test_an_executed_failure_blocks_without_inventing_a_grade(self):
        review = self.set_status("native-structure", "fail", [{"code": "missing_native_text"}])
        blockers = self.delivery.verify(review)
        self.assertEqual(blockers[0]["kind"], "presentation")
        self.assertEqual(blockers[0]["grade"], "")
        self.assertIn("native-structure", blockers[0]["name"])

    def test_a_missing_mandatory_check_is_invalid(self):
        self.delivery.inspections["reports"] = [entry for entry in self.delivery.inspections["reports"]
                                                if entry["check_id"] != "edit-save-reopen"]
        review = self.set_status("visual", "pass")
        with self.assertRaisesRegex(InputError, "not executed"):
            self.delivery.verify(review)

    def test_a_candidate_check_bound_to_another_subject_is_refused(self):
        for entry in self.delivery.inspections["reports"]:
            if entry["scope"] == "candidate":
                entry["subject_sha256"] = "1" * 64
        review = self.set_status("visual", "pass")
        with self.assertRaisesRegex(InputError, "another subject"):
            self.delivery.verify(review)

    def test_status_must_agree_with_its_findings(self):
        review = self.set_status("navigation", "pass", [{"code": "wrong_destination"}])
        with self.assertRaisesRegex(InputError, "contradicts"):
            self.delivery.verify(review)


class InventoryTests(PresentationFixture):
    """The delivered directory is read from disk, not from the manifest."""

    def test_an_undeclared_file_is_detected(self):
        (self.delivery.output / "extra.txt").write_text("not declared", encoding="utf-8")
        with self.assertRaisesRegex(InputError, "differ from the manifest"):
            self.delivery.verify()

    def test_a_declared_file_that_is_absent_is_detected(self):
        (self.delivery.output / "index.html").unlink()
        with self.assertRaises(InputError):
            self.delivery.verify()

    def test_a_file_outside_the_output_root_cannot_be_declared(self):
        manifest = copy.deepcopy(self.delivery.manifest)
        manifest["files"].append({"path": "reports/implementation.json", "role": "model",
                                  "sha256": self.delivery.sha(self.delivery.reports / "implementation.json"),
                                  "bytes": (self.delivery.reports / "implementation.json").stat().st_size})
        self.delivery.blob(self.delivery.reports / "cycle-01-presentation-manifest.json", json.dumps(manifest))
        review = self.delivery.review()
        review["presentation"] = self.delivery.block()
        with self.assertRaisesRegex(InputError, "output root"):
            self.delivery.verify(review)

    def test_unsafe_paths_are_refused(self):
        for name in ("../escape.html", "/absolute.html", "C:\\drive.html", "output/null\0.html"):
            manifest = copy.deepcopy(self.delivery.manifest)
            manifest["formats"]["html-offline"]["path"] = name
            self.delivery.blob(self.delivery.reports / "cycle-01-presentation-manifest.json",
                               json.dumps(manifest))
            review = self.delivery.review()
            review["presentation"] = self.delivery.block()
            with self.subTest(name=name), self.assertRaises(InputError):
                self.delivery.verify(review)

    def test_a_format_must_use_its_contracted_file_name(self):
        (self.delivery.output / "deck-editable.pptx").rename(self.delivery.output / "outro.pptx")
        manifest = copy.deepcopy(self.delivery.manifest)
        manifest["formats"]["pptx-editable"]["path"] = "output/presentation-cycle-01/outro.pptx"
        for item in manifest["files"]:
            if item["path"].endswith("deck-editable.pptx"):
                item["path"] = "output/presentation-cycle-01/outro.pptx"
        self.delivery.blob(self.delivery.reports / "cycle-01-presentation-manifest.json", json.dumps(manifest))
        review = self.delivery.review()
        review["presentation"] = self.delivery.block()
        with self.assertRaisesRegex(InputError, "deck-editable.pptx"):
            self.delivery.verify(review)


if __name__ == "__main__":
    unittest.main()
