from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.checks.common import InputError

HAS_ENGINE = all(importlib.util.find_spec(module) is not None
                 for module in ("pptx", "playwright", "PIL"))
ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "tests" / "fixtures" / "presentations" / "reference-deck.json"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_candidate(root: Path, cycle: int = 1):
    """Compose one candidate inside a synthetic swarm and return its result."""
    from scripts.presentations.build import build

    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(parents=True, exist_ok=True)
    (root / "brief.md").write_text("---\nswarm_id: engine-fixture\n---\n# fixture\n", encoding="utf-8")
    return build(DECK, root, root / "output" / f"presentation-cycle-{cycle:02d}",
                 profile="windows-powerpoint-v1", cycle=cycle)


@unittest.skipUnless(HAS_ENGINE, "Install requirements-presentations.txt to run the presentation engine tests")
class EngineFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.swarm = cls.root / "swarm"
        cls.result = build_candidate(cls.swarm)
        cls.layout = cls.result["layout"]
        cls.candidate = cls.result["destination"]

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        self.addCleanup(shutil.rmtree, self.work, True)

    def copy(self, name: str = "candidate") -> Path:
        target = self.work / name
        shutil.copytree(self.candidate, target)
        return target

    @staticmethod
    def rewrite(package: Path, part: str, change) -> None:
        """Replace one XML part of a saved PPTX, keeping every other entry."""
        with zipfile.ZipFile(package) as source:
            entries = [(item, source.read(item.filename)) for item in source.infolist()]
        temporary = package.with_suffix(".tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
            for item, data in entries:
                if item.filename == part:
                    data = change(data.decode("utf-8")).encode("utf-8")
                target.writestr(item, data)
        temporary.replace(package)

    def slide_part(self, package: Path, page_id: str) -> str:
        from scripts.presentations.inspect import read_package, slide_order

        order = slide_order(read_package(package))
        index = [page["page_id"] for page in self.layout["pages"]].index(page_id)
        return order[index]

    def codes(self, findings) -> set[str]:
        return {item["code"] for item in findings}


class DetectorTests(EngineFixture):
    """Each detector must accept the valid delivery and accuse a specific defect."""

    def test_the_untouched_candidate_passes_every_check(self):
        for check in self.result["checks"]:
            with self.subTest(check=check["check_id"]):
                self.assertEqual(check["findings"], [], check["check_id"])

    def test_removed_wording_is_detected_in_the_delivered_html(self):
        from scripts.presentations.inspect import inspect_html

        candidate = self.copy()
        page = candidate / "index.html"
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("reserva operacional", "", 1), encoding="utf-8")
        report = inspect_html(page, self.layout)
        self.assertIn("text_mismatch", self.codes(report["findings"]))

    def test_a_removed_control_and_a_weakened_policy_are_detected(self):
        from scripts.presentations.inspect import inspect_html

        candidate = self.copy()
        page = candidate / "index.html"
        text = page.read_text(encoding="utf-8")
        text = re.sub(r'<button[^>]*data-action-id="sys:nav:index"[^>]*>.*?</button>', "", text, count=1)
        text = text.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
        page.write_text(text, encoding="utf-8")
        report = inspect_html(page, self.layout)
        self.assertIn("missing_control", self.codes(report["findings"]))
        self.assertIn("weak_content_policy", self.codes(report["findings"]))

    def test_a_remote_resource_is_detected_in_the_offline_package(self):
        from scripts.presentations.inspect import inspect_html

        candidate = self.copy()
        page = candidate / "index.html"
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("</body>", '<img src="http://example.test/a.png" alt="x"></body>'),
                        encoding="utf-8")
        report = inspect_html(page, self.layout)
        self.assertIn("unsupported_scheme", self.codes(report["findings"]))

    def test_erased_native_text_is_detected_in_the_editable_deck(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-editable.pptx"
        part = self.slide_part(package, "origem-a")
        self.rewrite(package, part, lambda xml: re.sub(r"<a:t>[^<]{12,}</a:t>", "<a:t></a:t>", xml, count=1))
        report = inspect_pptx(package, self.layout, editable=True)
        self.assertIn("missing_native_text", self.codes(report["findings"]))

    def test_text_added_to_the_faithful_deck_is_detected(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-faithful.pptx"
        part = self.slide_part(package, "origem-a")
        self.rewrite(package, part, lambda xml: xml.replace(
            "</p:spTree>",
            '<p:sp><p:nvSpPr><p:cNvPr id="900" name="extra"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr/><p:txBody><a:bodyPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
            '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:r><a:t>Texto nao revisado</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>'))
        report = inspect_pptx(package, self.layout, editable=False)
        self.assertIn("unexpected_text_in_faithful", self.codes(report["findings"]))

    def test_a_support_page_shown_in_the_normal_sequence_is_detected(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-editable.pptx"
        part = self.slide_part(package, "sys:support:origem-a:detalhe:1")
        self.rewrite(package, part, lambda xml: xml.replace(' show="0"', "", 1))
        report = inspect_pptx(package, self.layout, editable=True)
        self.assertIn("slideshow_visibility", self.codes(report["findings"]))

    def test_content_moved_outside_the_stage_is_detected(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-editable.pptx"
        part = self.slide_part(package, "origem-a")
        self.rewrite(package, part, lambda xml: re.sub(r'<a:off x="\d+" y="\d+"/>',
                                                       '<a:off x="99999999" y="99999999"/>', xml, count=1))
        report = inspect_pptx(package, self.layout, editable=True)
        self.assertIn("outside_stage", self.codes(report["findings"]))

    def test_unanchored_connectors_and_external_relationships_are_detected(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-editable.pptx"
        part = self.slide_part(package, "origem-b")
        self.rewrite(package, part, lambda xml: re.sub(r"<a:stCxn[^/]*/>", "", xml, count=1))
        report = inspect_pptx(package, self.layout, editable=True)
        self.assertIn("unanchored_connectors", self.codes(report["findings"]))

        other = self.copy("relationship") / "deck-editable.pptx"
        rels = f"{part.rsplit('/', 1)[0]}/_rels/{part.rsplit('/', 1)[1]}.rels"
        self.rewrite(other, rels, lambda xml: xml.replace(
            "</Relationships>",
            '<Relationship Id="rIdExternal" Target="http://example.test/x.bin" TargetMode="External" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"/>'
            "</Relationships>"))
        report = inspect_pptx(other, self.layout, editable=True)
        self.assertIn("external_relationship", self.codes(report["findings"]))

    def test_removed_notes_are_detected(self):
        from scripts.presentations.inspect import inspect_pptx

        candidate = self.copy()
        package = candidate / "deck-editable.pptx"
        index = [page["page_id"] for page in self.layout["pages"]].index("origem-a") + 1
        self.rewrite(package, f"ppt/notesSlides/notesSlide{index}.xml",
                     lambda xml: re.sub(r"<a:t>[^<]*</a:t>", "<a:t></a:t>", xml))
        report = inspect_pptx(package, self.layout, editable=True)
        self.assertIn("missing_notes", self.codes(report["findings"]))

    def test_an_unsafe_or_oversized_package_is_refused_before_reading(self):
        from scripts.presentations.inspect import read_package

        hostile = self.work / "hostile.pptx"
        with zipfile.ZipFile(hostile, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("../escape.xml", "<x/>")
        with self.assertRaisesRegex(InputError, "unsafe part name"):
            read_package(hostile)
        bomb = self.work / "bomb.pptx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("ppt/presentation.xml", "0" * (4 * 1024 * 1024))
        with self.assertRaisesRegex(InputError, "expands beyond"):
            read_package(bomb)


class LifecycleTests(EngineFixture):
    """Building, re-inspecting and publishing never overwrite an evaluated delivery."""

    def test_a_destination_that_exists_is_never_replaced(self):
        from scripts.presentations.build import build

        before = (self.candidate / "index.html").read_bytes()
        with self.assertRaisesRegex(InputError, "must be new"):
            build(DECK, self.swarm, self.candidate, profile="windows-powerpoint-v1", cycle=1)
        self.assertEqual((self.candidate / "index.html").read_bytes(), before)

    def test_a_failed_composition_leaves_no_partial_candidate(self):
        from scripts.presentations.build import build

        broken = json.loads(DECK.read_text(encoding="utf-8"))
        broken["slides"][0]["title"] = [{"text": "Palavra " * 200}]
        source = self.work / "broken-deck.json"
        source.write_text(json.dumps(broken), encoding="utf-8")
        shutil.copy2(DECK.parent / "figura.png", self.work / "figura.png")
        destination = self.swarm / "output" / "presentation-cycle-09"
        with self.assertRaises(InputError):
            build(source, self.swarm, destination, profile="windows-powerpoint-v1", cycle=9)
        self.assertFalse(destination.exists())
        self.assertFalse([item for item in destination.parent.glob(".docswarm-presentation-*")])

    def test_rebuilding_the_same_deck_reproduces_the_same_pages_and_geometry(self):
        second = build_candidate(self.root / "swarm-again", cycle=2)
        self.assertEqual([page["page_id"] for page in second["layout"]["pages"]],
                         [page["page_id"] for page in self.layout["pages"]])
        self.assertEqual(second["layout"]["elements"], self.layout["elements"])
        self.assertEqual(second["layout"]["navigation"], self.layout["navigation"])
        self.assertEqual(second["manifest"]["deck"]["sha256"], self.result["manifest"]["deck"]["sha256"])

    def test_publication_requires_an_approved_gate_and_a_new_destination(self):
        from scripts.presentations.publish import acceptance, promote

        with self.assertRaisesRegex(InputError, "requires"):
            acceptance(self.swarm, 1)
        (self.swarm / "reports" / "cycle-01-review.yaml").write_text("{}", encoding="utf-8")
        (self.swarm / "reports" / "cycle-01-gate.json").write_text(
            json.dumps({"schema_version": 1, "exit_code": 1,
                        "result": {"cycle": 1, "outcome": "rejected", "blocked": []}}), encoding="utf-8")
        with self.assertRaisesRegex(InputError, "approved gate"):
            acceptance(self.swarm, 1)
        with self.assertRaisesRegex(InputError, "record the acceptance"):
            promote(self.swarm, 1, self.work / "delivery")

    def test_the_inspection_refuses_a_plan_from_another_deck(self):
        import contextlib
        import io

        from scripts.presentations.__main__ import main

        candidate = self.copy("other-deck")
        layout = json.loads((candidate / "layout.json").read_text(encoding="utf-8"))
        layout["deck_sha256"] = "0" * 64
        (candidate / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            self.assertEqual(main(["inspect", "--destination", str(candidate)]), 2)
        self.assertIn("another deck", captured.getvalue())

    def test_the_editorial_text_carries_every_delivered_word(self):
        from scripts.presentations.build import editorial_text

        text = editorial_text(self.layout)
        for excerpt in ("Critérios e condições do exemplo", "Abrir o detalhe do cálculo",
                        "Detalhe do cálculo e das condições", "Anterior", "Sumário",
                        "[notas de detalhe]", "Consultar a documentação citada"):
            self.assertIn(excerpt, text)


class IntegrationTests(EngineFixture):
    """A real candidate drives the gate, the report, the monitor and the memory."""

    QUOTES = ("Critérios e condições do exemplo", "O relatório compara capacidade",
              "A reserva operacional permanece", "Abrir o detalhe do cálculo",
              "Uma nova medição exige reavaliar")

    def swarm_with_delivery(self) -> tuple[Path, dict]:
        from scripts.presentations.__main__ import assemble

        root = self.work / "integration"
        result = build_candidate(root, cycle=1)
        reports = root / "reports"
        annotated = {"schema_version": 1, "capability": "presentation-v1",
                     "note": "Synthetic qualification fixture; not a real environment approval."}
        checks = lambda names: [{"check_id": name, "status": "pass", "findings": [], "observations": {}}
                                for name in names]
        (reports / "implementation-evidence.json").write_text(
            json.dumps({**annotated, "files": [], "checks": checks(
                ("compatibility", "adversarial-detectors", "lifecycle"))}), encoding="utf-8")
        (reports / "profile-evidence.json").write_text(
            json.dumps({**annotated, "profile": "windows-powerpoint-v1", "environment": {},
                        "checks": checks(("component-capability", "visual-font-calibration",
                                          "interactive-isolation"))}), encoding="utf-8")
        for name in ("implementation", "profile"):
            (reports / f"{name}.json").write_text(json.dumps({
                **annotated, "evidence": {"path": f"reports/{name}-evidence.json",
                                          "sha256": sha(reports / f"{name}-evidence.json")}}), encoding="utf-8")
        summary = assemble(root, 1, "windows-powerpoint-v1", result,
                           json.loads((reports / "implementation-evidence.json").read_text(encoding="utf-8"))["checks"],
                           json.loads((reports / "profile-evidence.json").read_text(encoding="utf-8"))["checks"])
        self.assertEqual(summary["status"], "pass", summary["reports"])
        (root / "brief.md").write_text(
            '---\nswarm_id: integration\nskill_version: "3.4.0"\nmode: document\n'
            "artifact_type: presentation\nmax_cycles: 3\nquality_contract: editorial-v1\n"
            "editorial_reviewer: reviewer-01-clarity\n"
            "presentation:\n  schema_version: 1\n  capability: presentation-v1\n"
            "  deck_path: output/presentation-cycle-01/deck.json\n  profile: windows-powerpoint-v1\n"
            "  required_formats:\n    - html-offline\n    - pptx-faithful\n    - pptx-editable\n"
            "deliverables:\n  - output/presentation-cycle-01/index.html\n"
            "  - output/presentation-cycle-01/deck-faithful.pptx\n"
            "  - output/presentation-cycle-01/deck-editable.pptx\n---\n# Integration fixture\n",
            encoding="utf-8")
        return root, {"summary": summary, "result": result}

    def review_for(self, root: Path, context: dict) -> dict:
        from scripts.checks import gate

        layout = context["result"]["layout"]
        pages = [page["page_id"] for page in layout["pages"]]
        rows = []
        for dimension in ("factual", "decision"):
            for topic in ("T01", "T02", "T03", "T04"):
                rows.append({"reviewer": "reviewer-02-content", "dimension": dimension, "topic_id": topic,
                             "grade": "A", "justification": "Annotated fixture judgment.", "action": ""})
        for dimension in ("legibility", "interaction"):
            for fmt in ("html-offline", "pptx-faithful", "pptx-editable"):
                for page in pages:
                    rows.append({"reviewer": "reviewer-03-visual", "dimension": dimension, "format": fmt,
                                 "page_id": page, "grade": "A",
                                 "justification": "Annotated fixture judgment.", "action": ""})
        for page in pages:
            rows.append({"reviewer": "reviewer-03-visual", "dimension": "editability",
                         "format": "pptx-editable", "page_id": page, "grade": "A",
                         "justification": "Annotated fixture judgment.", "action": ""})
        reports = root / "reports"
        for reviewer in ("reviewer-02-content", "reviewer-03-visual"):
            owned = [row for row in rows if row["reviewer"] == reviewer]
            (reports / f"cycle-01-{reviewer}.json").write_text(
                json.dumps({"schema_version": 1, "cycle": 1, "reviewer": reviewer,
                            "presentation_reviews": owned}), encoding="utf-8")
        output = root / "output" / "presentation-cycle-01"
        editorial = {
            "schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity", "scope": "full_document",
            "text": {"path": "reports/cycle-01-editorial-text.txt",
                     "sha256": sha(reports / "cycle-01-editorial-text.txt")},
            "artifacts": [{"path": f"output/presentation-cycle-01/{name}", "sha256": sha(output / name)}
                          for name in ("index.html", "deck-faithful.pptx", "deck-editable.pptx")],
            "surfaces": [{"surface": surface, "grade": "A", "location": surface, "quote": quote,
                          "justification": "Annotated fixture judgment about the delivered wording.",
                          "action": ""}
                         for surface, quote in zip(gate.EDITORIAL_SURFACES, self.QUOTES)],
            "findings": [],
        }
        (reports / "cycle-01-reviewer-01-clarity.json").write_text(
            json.dumps({"schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity",
                        "editorial": editorial}), encoding="utf-8")
        summary = context["summary"]
        return {"schema_version": 1, "skill_version": "3.4.0", "cycle": 1, "max_cycles": 3,
                "topics": [{"topico": topic, "nota_minima": "A",
                            "revisor_da_minima": "reviewer-02-content", "bloqueia": False}
                           for topic in ("T01", "T02", "T03", "T04")],
                "rubberduck": {"critico": False, "achados": []}, "editorial": editorial,
                "presentation": {"schema_version": 1, "capability": "presentation-v1", "cycle": 1,
                                 "profile": "windows-powerpoint-v1", "inputs": summary["inputs"],
                                 "manifest": summary["manifest"], "inspections": summary["inspections"],
                                 "reviews": rows}}

    def test_a_real_candidate_passes_the_gate_and_any_change_invalidates_it(self):
        import contextlib
        import io

        from scripts.checks import final_report, gate, progress, update_memory

        root, context = self.swarm_with_delivery()
        review = self.review_for(root, context)
        path = root / "reports" / "cycle-01-review.yaml"
        output = root / "reports" / "cycle-01-gate.json"

        def run() -> int:
            path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return gate.main([str(path), "--output", str(output)])

        self.assertEqual(run(), 0)
        (root / "reports" / "cycle-01-tables-check.json").write_text('{"failures":0}', encoding="utf-8")
        (root / "sources").mkdir(exist_ok=True)
        (root / "sources" / "sources-check.json").write_text(
            '{"counts":{"ok":1,"redirect":0,"warn":0,"fail":0}}', encoding="utf-8")
        self.assertIn("Presentation delivery", final_report.render(root))
        self.assertTrue(update_memory.approved(root))
        self.assertEqual(progress.snapshot(root)["cycles"][0]["gate"]["status"], "verified")

        delivery = root / "output" / "presentation-cycle-01"
        for name in ("index.html", "deck-editable.pptx", "deck-faithful.pptx", "deck.json",
                     "runtime/app.js", "fonts/Carlito-Regular.ttf"):
            target = delivery / name
            original = target.read_bytes()
            with self.subTest(changed=name):
                target.write_bytes(original + b" ")
                self.assertEqual(run(), 3)
                self.assertFalse(update_memory.approved(root))
                self.assertEqual(progress.snapshot(root)["cycles"][0]["gate"]["status"], "stale")
                target.write_bytes(original)
        self.assertEqual(run(), 0)

        low = json.loads(json.dumps(review))
        low["presentation"]["reviews"][0]["grade"] = "B+"
        low["presentation"]["reviews"][0]["action"] = "Rever a afirmação do tópico com o autor."
        owned = [row for row in low["presentation"]["reviews"] if row["reviewer"] == "reviewer-02-content"]
        (root / "reports" / "cycle-01-reviewer-02-content.json").write_text(
            json.dumps({"schema_version": 1, "cycle": 1, "reviewer": "reviewer-02-content",
                        "presentation_reviews": owned}), encoding="utf-8")
        review, keep = low, review
        self.assertEqual(run(), 1)
        review = keep
        owned = [row for row in review["presentation"]["reviews"] if row["reviewer"] == "reviewer-02-content"]
        (root / "reports" / "cycle-01-reviewer-02-content.json").write_text(
            json.dumps({"schema_version": 1, "cycle": 1, "reviewer": "reviewer-02-content",
                        "presentation_reviews": owned}), encoding="utf-8")
        self.assertEqual(run(), 0)

    def test_publication_copies_exactly_the_accepted_set(self):
        import contextlib
        import io

        from scripts.checks import gate
        from scripts.presentations.publish import acceptance, promote

        root, context = self.swarm_with_delivery()
        review = self.review_for(root, context)
        path = root / "reports" / "cycle-01-review.yaml"
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main([str(path), "--output", str(root / "reports" / "cycle-01-gate.json")]), 0)
        record = acceptance(root, 1)
        destination = self.work / "delivered"
        result = promote(root, 1, destination)
        self.assertEqual(len(result["files"]), len(record["files"]) + 1)
        for item in record["files"]:
            copied = destination / item["path"]
            self.assertTrue(copied.is_file(), item["path"])
            self.assertEqual(sha(copied), item["sha256"])
        self.assertTrue((destination / "output" / "presentation-cycle-01" / "deck-editable.pptx").is_file())
        with self.assertRaisesRegex(InputError, "must be new"):
            promote(root, 1, destination)
        with self.assertRaisesRegex(InputError, "already exists"):
            acceptance(root, 1)


if __name__ == "__main__":
    unittest.main()
