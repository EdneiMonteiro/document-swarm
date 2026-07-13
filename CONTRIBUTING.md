# Contributing

## Issues and pull requests

To reduce spam and keep this project maintainable, creating issues and pull
requests is restricted to collaborators with `write` access.

If you are not a collaborator:

- open a Discussion, if enabled; or
- contact the maintainer to propose the change.

For maintainers, use one issue per branch and pull request. Prefer branch names
such as `feat/issue-<n>-<slug>`.

The default branch is protected: deletion and force-pushes are disabled.

## Behavioral changes

`SKILL.md` is executable behavior, not ordinary prose. A pull request that
changes it must:

1. update `CHANGELOG.md`;
2. keep `skill_version` semantically correct;
3. update directly related documentation;
4. add or update a deterministic fixture when the changed behavior is
   mechanically testable;
5. run a small end-to-end fixture swarm before merge.

## Code

- Scripts in `scripts/checks/` must use Python 3 standard library only.
- Run `python3 -m unittest discover -s tests -v`.
- Scripts assist the coordinator; they must not pretend to replace semantic
  review by authors, reviewers or the rubber duck.
