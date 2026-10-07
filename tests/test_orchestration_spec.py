from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.checks.common import InputError
from scripts.orchestration import spec


def declaration(name: str, kind: str, *, extra: str = "", body: str = "Missão concreta.\n",
                model: str = "gpt-5.5", swarm: str = "demo", sources: int | None = None) -> str:
    sources = (5 if kind == "author" else 0) if sources is None else sources
    return (f"---\nname: {name}\nkind: {kind}\nrole: Papel de {name}\nmodel: {model}\n"
            f"reasoning_effort: high\ncontext_tier: long_context\nswarm: {swarm}\n"
            f"sources_min: {sources}\n{extra}---\n{body}")


class Roster:
    """A minimal valid swarm on disk, with hooks to break it one way at a time."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "agents" / "authors").mkdir(parents=True)
        (root / "agents" / "reviewers").mkdir(parents=True)
        (root / "brief.md").write_text(
            "---\nswarm_id: demo\nskill_version: \"3.6.0\"\nmax_cycles: 3\n"
            "editorial_reviewer: reviewer-02-clarity\ntopics:\n  T01: Enquadramento\n---\n# Demo\n", encoding="utf-8")
        self.put("authors/author-01-platform.md", declaration("author-01-platform", "author",
                 body="## Missão\nEscrever.\n\n## Tópicos\n- T01 Enquadramento\n- Alternativas\n\n## Como trabalhar\nx\n"))
        self.put("reviewers/reviewer-01-facts.md", declaration("reviewer-01-facts", "reviewer",
                 extra="evidence_class: fact\n", sources=5))
        self.put("reviewers/reviewer-02-clarity.md", declaration("reviewer-02-clarity", "reviewer",
                 extra="evidence_class: form\n"))
        self.put("coordinator.md", declaration("coordinator", "coordinator"))
        self.put("rubber-duck.md", declaration("rubber-duck", "rubber-duck"))

    def put(self, relative: str, text: str) -> Path:
        path = self.root / "agents" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.root.mkdir()
        self.swarm = Roster(self.root)

    def compile(self, **kwargs):
        return spec.compile_agents(self.root, **kwargs)

    def test_a_valid_swarm_compiles_with_least_privilege_tools_per_role(self):
        compiled = self.compile(models={"gpt-5.5"})
        self.assertEqual(compiled.errors, [])
        tools = {item.name: set(item.tools) for item in compiled.specs}
        self.assertIn("web_fetch", tools["author-01-platform"])
        self.assertIn("web_fetch", tools["reviewer-01-facts"])
        self.assertNotIn("web_fetch", tools["reviewer-02-clarity"])
        self.assertNotIn("web_fetch", tools["coordinator"])
        for granted in tools.values():
            self.assertFalse(granted & {"create", "edit", "powershell", "apply_patch", "bash"})

    def test_no_role_may_write_files_or_run_commands(self):
        for tools in spec.ROLE_TOOLS.values():
            self.assertFalse(set(tools) & {"create", "edit", "powershell", "apply_patch", "bash", "write_file"})

    def test_the_declaration_is_hashed_and_the_body_survives(self):
        author = self.compile().by_name("author-01-platform")
        self.assertEqual(len(author.sha256), 64)
        self.assertIn("Escrever.", author.body)
        self.assertNotIn("sources_min", author.body)
        self.assertIn("T01 Enquadramento", author.owned_topics_text)
        self.assertNotIn("Como trabalhar", author.owned_topics_text)

    def test_changing_the_declaration_changes_its_digest(self):
        before = self.compile().by_name("coordinator").sha256
        self.swarm.put("coordinator.md", declaration("coordinator", "coordinator", body="Outra missão.\n"))
        self.assertNotEqual(self.compile().by_name("coordinator").sha256, before)

    def test_every_problem_is_reported_at_once_not_just_the_first(self):
        self.swarm.put("authors/author-02.md", declaration("author-02", "author", model="inexistente-9", sources=2))
        self.swarm.put("reviewers/reviewer-03.md", declaration("reviewer-03", "reviewer", swarm="outro",
                                                               extra="evidence_class: fact\n", sources=5))
        compiled = self.compile(models={"gpt-5.5"})
        text = "\n".join(compiled.errors)
        self.assertIn("inexistente-9", text)
        self.assertIn("swarm must be 'demo'", text)
        self.assertGreaterEqual(len(compiled.errors), 2)
        self.assertTrue(any("below the 5" in item for item in compiled.warnings))

    def test_an_unavailable_model_is_rejected_before_any_dispatch(self):
        compiled = self.compile(models={"claude-sonnet-5.5"})
        self.assertTrue(any("not offered by this session" in item for item in compiled.errors))
        with self.assertRaises(InputError):
            compiled.require()

    def test_auto_is_always_accepted_and_unknown_availability_is_a_warning_not_a_pass(self):
        self.swarm.put("coordinator.md", declaration("coordinator", "coordinator", model="auto"))
        compiled = self.compile(models={"gpt-5.5"})
        self.assertEqual(compiled.errors, [])
        unverified = self.compile()
        self.assertEqual(unverified.errors, [])
        self.assertTrue(any("not verified" in item for item in unverified.warnings))

    def test_a_duplicate_name_is_rejected(self):
        self.swarm.put("authors/copy.md", declaration("author-01-platform", "author"))
        self.assertTrue(any("duplicate agent name" in item for item in self.compile().errors))

    def test_names_that_differ_only_by_case_are_one_name_because_they_become_one_file(self):
        # Both would write sources/fragments/<name>.json and reports/<cycle>-<name>.json: on Windows and macOS
        # that is one file, so the second agent would silently overwrite the first.
        self.swarm.put("reviewers/reviewer-03-case.md", declaration("Reviewer-01-Facts", "reviewer",
                                                                    extra="evidence_class: fact\n", sources=5))
        errors = self.compile().errors
        self.assertTrue(any("duplicate agent name 'Reviewer-01-Facts'" in item and "without regard to case" in item
                            for item in errors), errors)
        # The spelling that comes first in the folder is the one on record: the order must not matter.
        (self.root / "agents" / "reviewers" / "reviewer-03-case.md").unlink()
        self.swarm.put("reviewers/reviewer-00-case.md", declaration("REVIEWER-01-FACTS", "reviewer",
                                                                    extra="evidence_class: fact\n", sources=5))
        errors = self.compile().errors
        self.assertTrue(any("duplicate agent name 'reviewer-01-facts'" in item for item in errors), errors)

    def test_a_name_that_windows_reads_as_a_device_or_trims_is_refused(self):
        for name in ("con", "NUL", "aux.backup", "com1", "lpt9.x", "author-01.", "author-02-end."):
            with self.subTest(name=name):
                self.swarm.put("authors/odd.md", declaration(name, "author"))
                self.assertTrue(any("is not portable" in item for item in self.compile().errors), name)
        self.swarm.put("authors/odd.md", declaration("console-author", "author"))
        self.assertFalse(any("is not portable" in item for item in self.compile().errors),
                         "a name that merely starts like a device name is fine")

    def test_an_unsafe_name_is_rejected(self):
        self.swarm.put("authors/bad.md", declaration("../escape", "author"))
        self.assertTrue(any("name must be" in item for item in self.compile().errors))

    def test_a_declaration_cannot_widen_the_tools_of_its_role(self):
        self.swarm.put("reviewers/reviewer-02-clarity.md", declaration(
            "reviewer-02-clarity", "reviewer", extra="evidence_class: form\ntools:\n  - view\n  - powershell\n"))
        self.assertTrue(any("may not use (powershell)" in item for item in self.compile().errors))

    def test_a_declaration_may_narrow_the_tools_of_its_role(self):
        self.swarm.put("authors/author-01-platform.md", declaration(
            "author-01-platform", "author", extra="tools:\n  - view\n  - web_fetch\n"))
        author = self.compile().by_name("author-01-platform")
        self.assertEqual(set(author.tools), {"view", "web_fetch"})

    def test_a_declaration_without_a_mission_is_rejected(self):
        self.swarm.put("coordinator.md", declaration("coordinator", "coordinator", body="\n"))
        self.assertTrue(any("no body" in item for item in self.compile().errors))

    def test_invalid_effort_and_tier_are_rejected(self):
        text = declaration("coordinator", "coordinator").replace("reasoning_effort: high", "reasoning_effort: turbo")
        self.swarm.put("coordinator.md", text.replace("long_context", "gigantic"))
        joined = "\n".join(self.compile().errors)
        self.assertIn("reasoning_effort must be", joined)
        self.assertIn("context_tier must be", joined)

    def test_a_reviewer_without_an_evidence_class_is_inferred_and_flagged(self):
        self.swarm.put("reviewers/reviewer-02-clarity.md", declaration("reviewer-02-clarity", "reviewer"))
        compiled = self.compile()
        self.assertEqual(compiled.by_name("reviewer-02-clarity").evidence_class, "form")
        self.assertTrue(any("inferred 'form'" in item for item in compiled.warnings))

    def test_a_fact_reviewer_below_the_contract_minimum_is_warned_not_refused(self):
        self.swarm.put("reviewers/reviewer-01-facts.md", declaration(
            "reviewer-01-facts", "reviewer", extra="evidence_class: fact\n", sources=1))
        compiled = self.compile()
        self.assertEqual(compiled.errors, [])
        self.assertEqual(compiled.by_name("reviewer-01-facts").sources_min, 1)
        self.assertTrue(any("reviewer-01-facts" in item and "below the 5" in item for item in compiled.warnings))

    def test_an_author_that_cites_nothing_is_a_legitimate_declaration(self):
        # A real swarm declared an art-direction author with sources_min 0.
        self.swarm.put("authors/author-04-visual.md", declaration("author-04-visual", "author", sources=0))
        compiled = self.compile()
        self.assertEqual(compiled.errors, [])
        self.assertEqual(compiled.by_name("author-04-visual").sources_min, 0)

    def test_a_placeholder_effort_means_unset_and_a_real_typo_is_still_refused(self):
        for placeholder in ("standard", "padrão", "default", "auto"):
            with self.subTest(placeholder=placeholder):
                text = declaration("coordinator", "coordinator").replace("reasoning_effort: high",
                                                                          f"reasoning_effort: {placeholder}")
                self.swarm.put("coordinator.md", text)
                compiled = self.compile()
                self.assertEqual(compiled.errors, [])
                self.assertIsNone(compiled.by_name("coordinator").reasoning_effort)
        self.swarm.put("coordinator.md", declaration("coordinator", "coordinator").replace(
            "reasoning_effort: high", "reasoning_effort: turbo"))
        self.assertTrue(any("reasoning_effort must be" in item for item in self.compile().errors))

    def test_a_swarm_without_declarations_is_reported(self):
        empty = Path(self.temporary.name) / "empty"
        empty.mkdir()
        (empty / "brief.md").write_text("---\nswarm_id: empty\n---\n", encoding="utf-8")
        self.assertTrue(any("no agent declarations" in item for item in spec.compile_agents(empty).errors))


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.root.mkdir()
        self.swarm = Roster(self.root)
        self.brief = {"editorial_reviewer": "reviewer-02-clarity"}

    def test_a_complete_roster_has_no_problems(self):
        self.assertEqual(spec.check_roster(spec.compile_agents(self.root), self.brief), [])

    def test_every_required_role_is_checked(self):
        (self.root / "agents" / "rubber-duck.md").unlink()
        (self.root / "agents" / "coordinator.md").unlink()
        problems = spec.check_roster(spec.compile_agents(self.root), self.brief)
        self.assertIn("the swarm declares no rubber duck", problems)
        self.assertIn("the swarm declares no coordinator", problems)

    def test_two_coordinators_are_ambiguous(self):
        self.swarm.put("coordinator-2.md", declaration("coordinator-2", "coordinator"))
        self.assertIn("the swarm declares more than one coordinator",
                      spec.check_roster(spec.compile_agents(self.root), self.brief))

    def test_at_most_nine_authors_because_each_owns_a_hundred_source_identifiers(self):
        for index in range(2, 11):
            name = f"author-{index:02d}-extra"
            self.swarm.put(f"authors/{name}.md", declaration(name, "author"))
        problems = spec.check_roster(spec.compile_agents(self.root), self.brief)
        self.assertTrue(any("more than 9 authors" in item for item in problems), problems)
        (self.root / "agents" / "authors" / "author-10-extra.md").unlink()
        self.assertEqual(spec.check_roster(spec.compile_agents(self.root), self.brief), [],
                         "nine authors are the supported maximum")

    def test_the_editorial_reviewer_must_exist_and_judge_form(self):
        compiled = spec.compile_agents(self.root)
        self.assertTrue(any("not a declared reviewer" in item for item in
                            spec.check_roster(compiled, {"editorial_reviewer": "reviewer-99"})))
        self.assertTrue(any("must evaluate form" in item for item in
                            spec.check_roster(compiled, {"editorial_reviewer": "reviewer-01-facts"})))


class TopicTests(unittest.TestCase):
    def test_topics_are_a_mapping_of_short_ids_to_titles(self):
        self.assertEqual(spec.parse_topics({"topics": {"T01": " Enquadramento ", "T02": "Custos"}}),
                         {"T01": "Enquadramento", "T02": "Custos"})

    def test_missing_or_malformed_topics_are_refused(self):
        for brief in ({}, {"topics": []}, {"topics": ["T01"]}, {"topics": {"1 bad id": "x"}},
                      {"topics": {"T01": ""}}, {"topics": {"T01": 3}}):
            with self.subTest(brief=brief), self.assertRaises(InputError):
                spec.parse_topics(brief)


if __name__ == "__main__":
    unittest.main()
