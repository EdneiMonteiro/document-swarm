"""Check for newer commits on origin/main and fast-forward safely upon approval."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, args: list[str], *, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-pager", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _extract_skill_version(text: str) -> str:
    match = re.search(r'(?im)^skill_version\s*:\s*["\']?([^"\'\n\r]+)["\']?', text)
    return match.group(1).strip() if match else "unknown"


def check(
    repo: Path = DEFAULT_ROOT,
    *,
    remote: str = "origin",
    branch: str = "main",
    timeout: int = 5,
    fetch: bool = True,
) -> dict[str, Any]:
    """Return update availability between local HEAD and remote/branch."""
    root = repo.resolve(strict=True)
    skill_file = root / "SKILL.md"
    local_version = _extract_skill_version(skill_file.read_text(encoding="utf-8")) if skill_file.is_file() else "unknown"

    head_proc = _git(root, ["rev-parse", "--short", "HEAD"], timeout=timeout)
    if head_proc.returncode != 0:
        raise InputError(f"not a valid git repository: {root}")
    local_head = head_proc.stdout.strip()

    branch_proc = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=timeout)
    current_branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else "unknown"

    status_proc = _git(root, ["status", "--porcelain", "--untracked-files=no"], timeout=timeout)
    dirty = bool(status_proc.stdout.strip()) if status_proc.returncode == 0 else False

    if fetch:
        try:
            fetch_proc = _git(root, ["fetch", "--quiet", remote, branch], timeout=timeout)
            if fetch_proc.returncode != 0:
                return {
                    "schema_version": 1,
                    "status": "offline",
                    "update_available": False,
                    "local_version": local_version,
                    "local_head": local_head,
                    "current_branch": current_branch,
                    "dirty": dirty,
                    "detail": fetch_proc.stderr.strip() or "git fetch failed",
                }
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {
                "schema_version": 1,
                "status": "offline",
                "update_available": False,
                "local_version": local_version,
                "local_head": local_head,
                "current_branch": current_branch,
                "dirty": dirty,
                "detail": str(exc),
            }

    remote_ref = f"{remote}/{branch}"
    rem_proc = _git(root, ["rev-parse", "--short", remote_ref], timeout=timeout)
    if rem_proc.returncode != 0:
        return {
            "schema_version": 1,
            "status": "offline",
            "update_available": False,
            "local_version": local_version,
            "local_head": local_head,
            "current_branch": current_branch,
            "dirty": dirty,
            "detail": f"remote ref {remote_ref} not found",
        }
    remote_head = rem_proc.stdout.strip()

    behind_proc = _git(root, ["rev-list", "--count", f"HEAD..{remote_ref}"], timeout=timeout)
    ahead_proc = _git(root, ["rev-list", "--count", f"{remote_ref}..HEAD"], timeout=timeout)
    behind = int(behind_proc.stdout.strip()) if behind_proc.returncode == 0 and behind_proc.stdout.strip().isdigit() else 0
    ahead = int(ahead_proc.stdout.strip()) if ahead_proc.returncode == 0 and ahead_proc.stdout.strip().isdigit() else 0

    show_proc = _git(root, ["show", f"{remote_ref}:SKILL.md"], timeout=timeout)
    remote_version = _extract_skill_version(show_proc.stdout) if show_proc.returncode == 0 else local_version

    commits: list[str] = []
    if behind > 0:
        log_proc = _git(root, ["log", "--oneline", "-n", "10", f"HEAD..{remote_ref}"], timeout=timeout)
        if log_proc.returncode == 0:
            commits = [line.strip() for line in log_proc.stdout.splitlines() if line.strip()]

    update_available = behind > 0
    can_fast_forward = update_available and ahead == 0 and not dirty
    return {
        "schema_version": 1,
        "status": "update_available" if update_available else "up_to_date",
        "update_available": update_available,
        "can_fast_forward": can_fast_forward,
        "local_version": local_version,
        "remote_version": remote_version,
        "local_head": local_head,
        "remote_head": remote_head,
        "current_branch": current_branch,
        "behind": behind,
        "ahead": ahead,
        "dirty": dirty,
        "commits": commits,
    }


def apply_update(
    repo: Path = DEFAULT_ROOT,
    *,
    remote: str = "origin",
    branch: str = "main",
    timeout: int = 10,
    fetch: bool = True,
) -> dict[str, Any]:
    """Fast-forward the local repository to remote/branch if clean and approved."""
    root = repo.resolve(strict=True)
    info = check(root, remote=remote, branch=branch, timeout=timeout, fetch=fetch)
    if not info.get("update_available"):
        return {**info, "applied": False}
    if info.get("dirty"):
        raise InputError("local repository has uncommitted changes in tracked files; commit or stash before updating")
    if (info.get("ahead") or 0) > 0:
        raise InputError(f"local branch is ahead of {remote}/{branch} by {info['ahead']} commit(s); refusing non-fast-forward update")

    if info.get("current_branch") != branch:
        co_proc = _git(root, ["checkout", branch], timeout=timeout)
        if co_proc.returncode != 0:
            raise InputError(f"could not switch to {branch}: {co_proc.stderr.strip()}")

    merge_proc = _git(root, ["merge", "--ff-only", f"{remote}/{branch}"], timeout=timeout)
    if merge_proc.returncode != 0:
        raise InputError(f"fast-forward update failed: {merge_proc.stderr.strip()}")

    updated = check(root, remote=remote, branch=branch, timeout=timeout, fetch=False)
    return {
        **updated,
        "status": "updated",
        "applied": True,
        "previous_head": info.get("local_head"),
        "previous_version": info.get("local_version"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or apply fast-forward updates for document-swarm.")
    parser.add_argument("repo", nargs="?", type=Path, default=DEFAULT_ROOT, help="Path to document-swarm clone")
    parser.add_argument("--remote", default="origin", help="Git remote name (default: origin)")
    parser.add_argument("--branch", default="main", help="Target branch name (default: main)")
    parser.add_argument("--timeout", type=int, default=5, help="Git network timeout in seconds")
    parser.add_argument("--no-fetch", action="store_true", help="Do not run git fetch before comparing refs")
    parser.add_argument("--apply", action="store_true", help="Apply fast-forward update after user approval")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = apply_update(
                args.repo,
                remote=args.remote,
                branch=args.branch,
                timeout=max(args.timeout, 10),
                fetch=not args.no_fetch,
            )
        else:
            result = check(
                args.repo,
                remote=args.remote,
                branch=args.branch,
                timeout=args.timeout,
                fetch=not args.no_fetch,
            )
    except (OSError, InputError) as exc:
        print(json.dumps({"schema_version": 1, "status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
