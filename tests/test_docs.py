"""Enforce the documentation invariants this repository keeps breaking by hand.

Two checks lived as throwaway scripts and each one caught a real defect: a link
whose anchor died when a section was renamed, and a diagram that was shipped
without ever being rendered.  They are tests now so the next person does not
have to remember them.

These are structural checks.  They prove a diagram parses as a diagram, not that
it is legible: layout is judged by rendering it and looking.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = "`" * 3
# The vocabulary mermaid accepts on the first line of a block.
DIAGRAMS = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "pie", "quadrantChart",
    "requirementDiagram", "gitGraph", "mindmap", "timeline", "zenuml",
    "sankey-beta", "xychart-beta", "block-beta", "packet-beta", "architecture-beta",
)
PAIRS = {"[": "]", "(": ")", "{": "}"}


def documents() -> list[Path]:
    found = sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("docs/**/*.md"))
    if not found:
        raise AssertionError("no documentation found; the test would pass vacuously")
    return found


def anchor(title: str) -> str:
    """Reproduce the identifier GitHub derives from a heading.

    Punctuation is removed without collapsing the space it leaves behind, which
    is why an em dash produces two hyphens.
    """
    text = re.sub(r"`|\*|_", "", title.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text).strip("-")


def headings(text: str) -> set[str]:
    return {anchor(line.lstrip("#").strip()) for line in text.splitlines()
            if line.startswith("#") and line.lstrip("#").strip()}


def without_code(text: str) -> str:
    """Blank out fenced blocks so a sample inside them is not read as a real link."""
    kept, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith(FENCE):
            fenced = not fenced
            kept.append("")
            continue
        kept.append("" if fenced else line)
    return "\n".join(kept)


def mermaid_blocks(text: str) -> list[tuple[int, str]]:
    """Return each mermaid block with the line its fence opened on."""
    found: list[tuple[int, str]] = []
    body: list[str] | None = None
    start = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if body is None and line.strip() == FENCE + "mermaid":
            body, start = [], number
            continue
        if body is not None and line.strip() == FENCE:
            found.append((start, "\n".join(body)))
            body = None
            continue
        if body is not None:
            body.append(line)
    if body is not None:
        found.append((start, None))  # type: ignore[arg-type]
    return found


def fences(text: str) -> list[int]:
    """Return the line number of every fence marker, indented or not."""
    return [number for number, line in enumerate(text.splitlines(), start=1)
            if line.strip().startswith(FENCE)]


def unbalanced(label: str) -> str | None:
    """Report the first bracket or quote that never closes, ignoring escaped ones."""
    stack: list[str] = []
    quoted = False
    index = 0
    while index < len(label):
        char = label[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif not quoted and char in PAIRS:
            stack.append(PAIRS[char])
        elif not quoted and char in PAIRS.values():
            if not stack or stack.pop() != char:
                return f"unexpected {char!r}"
        index += 1
    if quoted:
        return "unterminated quote"
    if stack:
        return f"missing {stack[-1]!r}"
    return None


def scan(root: Path, files: list[Path]) -> list[str]:
    """Report every internal link whose file or anchor does not resolve."""
    anchors = {path.relative_to(root).as_posix(): headings(path.read_text(encoding="utf-8"))
               for path in files}
    broken = []
    for path in files:
        body = without_code(path.read_text(encoding="utf-8"))
        for _, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body):
            if target.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            name, _, fragment = target.partition("#")
            destination = (path.parent / name).resolve() if name else path.resolve()
            where = path.relative_to(root).as_posix()
            if not destination.exists():
                broken.append(f"{where}: missing target -> {target}")
                continue
            if not fragment:
                continue
            key = destination.relative_to(root).as_posix()
            if key in anchors and fragment not in anchors[key]:
                broken.append(f"{where}: missing anchor -> {target}")
    return broken


def counted(root: Path, files: list[Path]) -> int:
    total = 0
    for path in files:
        for _, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", without_code(path.read_text(encoding="utf-8"))):
            if not target.startswith(("http://", "https://", "mailto:", "#!")):
                total += 1
    return total


class LinkTests(unittest.TestCase):
    def test_every_internal_link_and_anchor_resolves(self):
        files = documents()
        self.assertGreater(counted(ROOT, files), 20,
                           "the link scan found almost nothing; it is not exercising the docs")
        self.assertEqual(scan(ROOT, files), [])

    def test_the_anchor_rule_matches_how_github_derives_one(self):
        self.assertEqual(anchor("## Fase 3 — Loop determinístico por ciclo"),
                         "fase-3--loop-determinístico-por-ciclo")
        self.assertEqual(anchor("### 2.6. Vigia de saúde e retomada"), "26-vigia-de-saúde-e-retomada")
        self.assertEqual(anchor("## `health.py` em uso"), "healthpy-em-uso")

    def test_the_same_scan_reports_a_planted_break(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "guia.md").write_text("# Guia\n\n## Seção viva\n", encoding="utf-8")
            good = root / "indice.md"
            good.write_text("[ok](./guia.md#seção-viva)\n", encoding="utf-8")
            files = [root / "guia.md", good]
            self.assertEqual(scan(root, files), [], "the control starts from a clean tree")
            good.write_text("[âncora morta](./guia.md#secao-renomeada)\n"
                            "[arquivo ausente](./sumiu.md)\n", encoding="utf-8")
            found = scan(root, files)
            self.assertEqual(len(found), 2, found)
            self.assertTrue(any("missing anchor" in item for item in found), found)
            self.assertTrue(any("missing target" in item for item in found), found)

    def test_code_samples_are_not_read_as_links(self):
        text = f"{FENCE}text\n[exemplo](./nao-existe.md)\n{FENCE}\n[real](./SKILL.md)\n"
        stripped = without_code(text)
        self.assertNotIn("nao-existe", stripped)
        self.assertIn("SKILL.md", stripped)


class MermaidTests(unittest.TestCase):
    def blocks(self) -> list[tuple[str, int, str]]:
        found = []
        for path in documents():
            for line, body in mermaid_blocks(path.read_text(encoding="utf-8")):
                found.append((path.relative_to(ROOT).as_posix(), line, body))
        return found

    def test_the_repository_actually_contains_diagrams(self):
        self.assertGreaterEqual(len(self.blocks()), 3, "nothing to check; the diagram tests would be vacuous")

    def test_every_block_is_closed_and_declares_a_known_diagram(self):
        problems = []
        for name, line, body in self.blocks():
            if body is None:
                problems.append(f"{name}:{line}: the mermaid fence is never closed")
                continue
            first = next((item.strip() for item in body.splitlines() if item.strip()), "")
            if not first.split(" ")[0].rstrip(";") in DIAGRAMS:
                problems.append(f"{name}:{line}: unknown diagram type {first!r}")
        self.assertEqual(problems, [])

    def test_every_fenced_block_is_closed(self):
        """A dropped closing fence is swallowed by the next block, so count them.

        Without this, removing the fence that closes a diagram leaves the diagram
        absorbing the prose below it while still parsing as a diagram.
        """
        problems = []
        for path in documents():
            text = path.read_text(encoding="utf-8")
            self.assertEqual([], [line for line in text.splitlines() if line.strip().startswith("`" * 4)],
                             f"{path.name}: nested fences would invalidate the parity check")
            marks = fences(text)
            if len(marks) % 2:
                problems.append(f"{path.relative_to(ROOT).as_posix()}: "
                                f"{len(marks)} fence markers; one block is never closed "
                                f"(last at line {marks[-1]})")
        self.assertEqual(problems, [])

    def test_no_diagram_swallowed_another_block(self):
        problems = []
        for name, line, body in self.blocks():
            if body is None:
                continue
            for offset, text in enumerate(body.splitlines(), start=1):
                if text.strip().startswith(FENCE):
                    problems.append(f"{name}:{line + offset}: a fence inside a diagram means "
                                    f"the diagram was never closed")
        self.assertEqual(problems, [])

    def test_every_node_and_edge_label_is_balanced(self):
        problems = []
        for name, line, body in self.blocks():
            if body is None:
                continue
            for offset, text in enumerate(body.splitlines(), start=1):
                stripped = text.split("%%", 1)[0]
                fault = unbalanced(stripped)
                if fault:
                    problems.append(f"{name}:{line + offset}: {fault} in {stripped.strip()!r}")
        self.assertEqual(problems, [])

    def test_the_balance_check_detects_real_breakage(self):
        self.assertIsNone(unbalanced('  A["texto (com) parênteses"] --> B'))
        self.assertIsNone(unbalanced('  TICK -->|"rótulo em duas<br/>linhas"| ART'))
        self.assertEqual(unbalanced('  A[sem fechar --> B'), "missing ']'")
        self.assertEqual(unbalanced('  A["aspas soltas] --> B'), "unterminated quote")
        self.assertEqual(unbalanced("  A) --> B"), "unexpected ')'")

    def test_an_unknown_diagram_type_is_detected(self):
        blocks = mermaid_blocks(f"{FENCE}mermaid\nflowchartz LR\n  A --> B\n{FENCE}\n")
        first = blocks[0][1].splitlines()[0].strip()
        self.assertNotIn(first.split(" ")[0], DIAGRAMS)

    def test_an_unclosed_fence_is_detected(self):
        blocks = mermaid_blocks(f"{FENCE}mermaid\nflowchart LR\n  A --> B\n")
        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0][1])

    def test_an_unclosed_fence_is_detected_even_when_a_later_block_closes_it(self):
        text = (f"{FENCE}mermaid\nflowchart LR\n  A --> B\n\n"
                f"Texto que deveria estar fora do diagrama.\n\n"
                f"{FENCE}powershell\nGet-Date\n{FENCE}\n")
        self.assertEqual(len(fences(text)) % 2, 1, "the parity check must see the missing fence")
        swallowed = [line for line in mermaid_blocks(text)[0][1].splitlines()
                     if line.strip().startswith(FENCE)]
        self.assertEqual(swallowed, [f"{FENCE}powershell"],
                         "the diagram absorbed the next block, which is the signal")


if __name__ == "__main__":
    unittest.main()
