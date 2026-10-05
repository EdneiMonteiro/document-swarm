from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MonitorInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.package = self.base / "package"
        self.scripts = self.package / "scripts"
        self.scripts.mkdir(parents=True)
        self.source = self.package / ".github" / "extensions" / "document-swarm-monitor"
        (self.source / "ui").mkdir(parents=True)
        (self.source / "extension.mjs").write_text("// fixture\n", encoding="utf-8")
        (self.source / "ui" / "index.html").write_text("<title>fixture</title>\n", encoding="utf-8")
        (self.source / "keep.txt").write_text("do not remove", encoding="utf-8")
        self.pdf_source = self.package / ".github" / "extensions" / "document-swarm-pdf"
        self.pdf_source.mkdir()
        (self.pdf_source / "extension.mjs").write_text("// optional PDF fixture\n", encoding="utf-8")
        (self.package / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
        for name in ("install.ps1", "install.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.scripts / name)

    def executable(self, shell):
        if shell == "powershell":
            result = shutil.which("pwsh")
        elif sys.platform == "win32":
            git = shutil.which("git")
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe" if git else None
            result = str(candidate) if candidate and candidate.exists() else None
        else:
            result = shutil.which("bash")
        if not result:
            self.skipTest(f"{shell} unavailable")
        return result

    def install(self, shell, home, without=False, pdf=False):
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "BASH_ENV": "",
               "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "MSYS": "winsymlinks:nativestrict"}
        if shell == "powershell":
            args = [self.executable(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.scripts / "install.ps1")]
            if without:
                args.append("-WithoutMonitor")
            if pdf:
                args.append("-WithPdf")
        else:
            args = [self.executable(shell), "install.sh"]
            if without:
                args.append("--without-monitor")
            if pdf:
                args.append("--with-pdf")
        return subprocess.run(args, cwd=self.scripts, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def check_install(self, shell):
        home = self.base / "home"
        skill = home / ".copilot" / "skills" / "document-swarm"
        monitor = home / ".copilot" / "extensions" / "document-swarm-monitor"
        for _ in range(2):
            result = self.install(shell, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(skill.resolve(), self.package.resolve())
            self.assertEqual(monitor.resolve(), self.source.resolve())
        result = self.install(shell, home, without=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(monitor.exists())
        self.assertEqual(skill.resolve(), self.package.resolve())
        self.assertEqual((self.source / "keep.txt").read_text(encoding="utf-8"), "do not remove")
        self.assertTrue((self.source / "extension.mjs").exists())

    def check_collision(self, shell):
        home = self.base / "home"
        monitor = home / ".copilot" / "extensions" / "document-swarm-monitor"
        monitor.mkdir(parents=True)
        (monitor / "user.txt").write_text("user data", encoding="utf-8")
        result = self.install(shell, home)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((home / ".copilot" / "skills" / "document-swarm").exists())
        self.assertEqual((monitor / "user.txt").read_text(encoding="utf-8"), "user data")
        result = self.install(shell, home, without=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((monitor / "user.txt").exists())

    def test_powershell_managed_install_and_opt_out(self):
        self.check_install("powershell")

    def test_bash_managed_install_and_opt_out(self):
        self.check_install("bash")

    def test_powershell_collision_is_non_destructive(self):
        self.check_collision("powershell")

    def test_bash_collision_is_non_destructive(self):
        self.check_collision("bash")

    def test_bash_source_has_portable_line_endings(self):
        raw = (ROOT / "scripts" / "install.sh").read_bytes()
        self.assertTrue(raw.startswith(b"#!/bin/bash\n"))
        self.assertNotIn(b"\r\n", raw)

    def check_pdf_install(self, shell):
        home = self.base / "home"
        link = home / ".copilot" / "extensions" / "document-swarm-pdf"
        result = self.install(shell, home, without=True, pdf=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), self.pdf_source.resolve())
        result = self.install(shell, home, without=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), self.pdf_source.resolve(), "omitting --with-pdf must not delete an existing PDF extension")

    def test_powershell_optional_pdf_install(self):
        self.check_pdf_install("powershell")

    def test_bash_optional_pdf_install(self):
        self.check_pdf_install("bash")

    def test_pdf_preflight_without_monitor_rejects_missing_source(self):
        (self.pdf_source / "extension.mjs").unlink()
        for shell in ("powershell", "bash"):
            home = self.base / shell
            with self.subTest(shell=shell):
                result = self.install(shell, home, without=True, pdf=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((home / ".copilot" / "skills" / "document-swarm").exists())
                self.assertFalse((home / ".copilot" / "extensions" / "document-swarm-pdf").exists())

    def test_pdf_collision_does_not_create_partial_installation(self):
        for shell in ("powershell", "bash"):
            home = self.base / shell
            destination = home / ".copilot" / "extensions" / "document-swarm-pdf"
            destination.mkdir(parents=True)
            original = destination / "user.txt"
            original.write_text("Preserve this directory", encoding="utf-8")
            with self.subTest(shell=shell):
                result = self.install(shell, home, pdf=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(original.read_text(encoding="utf-8"), "Preserve this directory")
                self.assertFalse((home / ".copilot" / "skills" / "document-swarm").exists())
                self.assertFalse((home / ".copilot" / "extensions" / "document-swarm-monitor").exists())
