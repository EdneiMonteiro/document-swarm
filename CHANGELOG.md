# Changelog

All notable changes to this skill are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the skill uses semantic versioning for behavior changes in `SKILL.md`.

## [3.6.0] - 2026-10-06

### Added

- Opt-in deterministic executor (`python scripts/orchestration`). Code runs the
  cycle that the coordinator used to carry in model turns: authors, consolidation,
  the mechanical checks in parallel, reviewers, the matrix, the rubber duck, the
  gate, the feedback of the next round and the delivery. Agents keep authorship,
  consolidation, independent review and the audit; `gate.py` keeps sole authority
  over approval, and every quality requirement is unchanged. Commands: `init`,
  `next`, `record`, `status`, `run`, `qualify` and `metrics`. Guide:
  `docs/executor.md`, `SKILL.md` section 2.7.
- The engine derives every answer from artifacts and an append-only journal, so a
  crash loses nothing and the same command resumes. It writes the files the
  coordinator flow wrote, which the unmodified legacy readers accept.
- Agents return JSON validated against a per-role contract: exact topic coverage,
  quotes present in the reviewed text, enough sources for fact reviewers, ownership
  of section files, and safe portable paths (no traversal, drive or stream
  characters, reserved Windows names, trailing dots, case collisions or links out
  of the swarm). A malformed answer is refused at once with the exact reason and a
  retry prompt that carries it.
- Verdicts are never trusted from a file. The recorded gate result is a cache bound
  to the review's bytes and stands only if the matrix still derives from the
  reviewers' grades and `gate.py`'s own evaluation reproduces it. An edited or
  forged record, a changed deliverable and an altered rejected cycle are refused
  instead of re-run, and source verdicts are snapshotted per round so the final
  recheck cannot reopen a review for a timestamp.
- Accepted results are attested. The journal entry that accepts a result carries the
  digest of the stored result, and a record that no longer matches blocks the stage
  (`result_altered`) instead of being trusted or paid for again. Reviewers' grades and
  the rubber duck's veto are read from these verified records; the files under
  `reports/` are a copy, rewritten from them at the matrix stage and at delivery.
- A result is accepted only for an attempt the engine issued; a reviewer's identity
  covers the sources index its prompt shows; the final source recheck is bound to the
  index it checked (`final_sources_changed`); a crash between inserting the narrative
  and journaling it neither duplicates it nor pays the agent twice.
- Source text and addresses are untrusted: a title or type may not carry an address, a
  pipe, a line break or control characters (the index is a table that
  `verify_sources.py` scans for addresses), and addresses that name this machine, a
  private network, credentials or a number in an unusual form are refused.
  `DOCSWARM_ALLOW_LOCAL_URLS=1` is a lab switch, set by the tests.
- Hostile or odd answers are refusals of that attempt, never exceptions: deeply nested
  text, a file that is also a folder, a Windows short name such as `INTROD~1.MD`, a path
  that resolves to another name, a path too long for the swarm folder, and anything a
  validator did not foresee. The other agents' results of the same step are recorded
  before a failure is raised.
- One `run` per swarm (`reports/execution/.run.lock`): a second run is refused at once,
  without paying any agent or touching the first run's heartbeat. Ctrl+C stops the agents
  that are running before anything waits for them, starts nothing new, and keeps what had
  already finished.
- A checker that crashes no longer reads as one that found something. `verify_sources.py`,
  `verify_tables.py` and `gate.py` exit 3 on an internal failure (Python's default, 1, is
  the status of "findings" and of "rejected"), and the engine requires a fresh, readable
  report from each checker whatever its exit status says.
- Agent names are unique by case-folded identity and may not be Windows device names or
  end in a dot, because they become file names. `init` refuses a brief the gate cannot
  approve (not `editorial-v1`, an editorial reviewer not named `reviewer-*`) and, on
  Windows, a swarm folder too deep for the executor's own files, before any agent is paid.
- `gate.py` refuses a matrix that says `critico: false` beside a finding marked critical.
- Prompts tell every agent that what it receives to analyse is data, never instruction.
- A failing source goes back only to the authors that cited it, and a failing table to the
  author whose section holds it; what cannot be attributed goes to everyone. The attribution
  is stored with the feedback when the repair starts.
- `run` stops the heartbeat race that could leave `driver.json` saying "running" after the
  run was interrupted, lists an agent as running only when its process began, and journals
  `task_started` so that `metrics` measures an agent from its real start, not from the
  moment it was issued before a stop and a resume.
- `run` executes each agent as one non-interactive `copilot` process, in parallel,
  with only the tools of its role, shell and writes denied, an empty scratch folder,
  no custom instructions and the prompt on stdin. Results are recorded as they
  arrive. A timeout or cancel ends the whole process tree and never waits on a pipe
  an orphan holds. It prints a table every minute and keeps
  `reports/execution/driver.json` as a heartbeat.
- `qualify` checks the CLI with a handful of minimal real calls (stdin prompt, no
  write under read-only tools, no read outside the agent's own folder, a web tool
  under the restriction, parallelism, the model in the usage record) and refuses to
  run without `--yes` because it spends
  credits. `run --plan-only` shows the first agents and their exact commands without
  running anything.
- `health.py` understands executor runs: it reads the heartbeat and the journal,
  classifies them as active, stalled or closed and prints the command that resumes, but
  only when the run is stalled. The executor's own journal decides before the artifacts of
  a cycle that may have been escalated before a person raised the ceiling, only a last
  event of `run_finished` closes a run, and a heartbeat dated in the future is not trusted.
- `metrics` decomposes an execution into agent time, code time and idle time, for
  executor journals and for monitor journals.
- `DOCSWARM_NO_REAL_CLI=1` makes any path to the real `copilot` fail. The tests set
  it, so a test, or a rule deliberately disabled by mutation testing, can never
  spend credits.
- `init` and `run` take `--max-cycles N`, which replaces the brief's cycle ceiling. It is
  kept in the plan until another is given, and a change is journaled as
  `max_cycles_changed` (previous, new and the brief's own). After an escalation,
  `run --max-cycles N` continues from the last cycle: the escalated verdict is withdrawn
  and the cycle gated again from grades that already exist, so only the new cycle is paid
  for. The rubber duck's identity no longer includes the ceiling, which is the only part
  of a task that changed with it. A swarm created before this change pays that one audit
  again.
- An agent's `copilot` process starts only the MCP servers its task has a tool of. The
  backend asks the CLI once per run (`copilot mcp list`) which servers exist and passes
  `--disable-mcp-server` for the others and `--disable-builtin-mcps` when no builtin one
  is needed. `--available-tools` already hid their tools, so an agent loses nothing, and a
  listing that fails or is not recognised stops nothing. `--keep-mcp-servers` restores the
  CLI's default. Measured on a trivial call with the backend's own flags (three alternating
  calls of each kind): a median of 44.1 s with the user's thirteen servers, 11.5 s without.
  Building a command (`command()`) starts nothing; the listing is asked for by the code that
  runs or previews a task.
- The grade a swarm authorizes is no longer only what a review says about itself. `gate.py`
  refuses (exit 3) a review that declares less than the swarm authorizes: the executor's
  `--approval-grade` (kept in the plan), the brief's `approval_grade`, or the original `A`.
  `progress.py` (monitor, `health.py`, `resume.py`) uses the same function. The plan that
  holds the option is itself attested: it counts only if its digest matches its content and
  the journal recorded that digest when it was written (the journal is written first, so a
  stop in between leaves a plan the journal knows). An edited plan is refused by every call
  that reads it, and a plan deleted after the journal says it was written and agents were
  paid for is refused instead of being replaced by the policy of a new swarm. A person who
  has no backup starts the plan again by stating the policy (`init` or `run` with
  `--approval-grade`): the new plan is built from the brief, the declarations and the options
  given, nothing of the old one is read, and the journal records `plan_recovered` with the
  reason and the grade. A swarm driven by
  `next` and `record` alone never had a plan, and that is not an error. For that one case
  the gate stands down (a non-empty executor journal and no `plan.json`): the grade of such a
  swarm is the executor's own default, which the gate cannot know, and the executor does not
  trust a review it did not render from that policy.
- The monitor panel, `health.py` and `resume.py` read the executor's plan: they show the
  ceiling given with `--max-cycles` and the swarm's approval grade, and the panel colours
  each grade, counts the topics that pass and words its legend against the grade the cycle's
  own review was judged under, so an `A-` approved at `A-` is no longer drawn as a failure.
  The decisions are pure functions with tests, and a test keeps a hard-coded pass mark out
  of the panel's source and page.
- The visual monitor is fed by the executor. `scripts/checks/executor_view.py` projects the
  executor's journal and heartbeat into the events the panel already knows (phase, dispatch,
  state of an agent, handoff, a note for the history, the end of the run), from a cursor and
  the epoch of the journal, and only reads: the executor does not know the panel exists and
  there is no channel from it to the extension. The extension applies each reading as one
  `executor_batch` event (one journal line, one snapshot), after validating every event whole
  and copying it field by field; what the state cannot hold is skipped, counted and shown, and
  never half applied. The panel shows who is running and for how long ("Executando · 3 min
  20 s"), what each agent took (from the journal's times, never from the `seconds` of a
  record, which run from the issue and include every stop of the executor), the handoffs
  between roles, a refusal with its reason, the executor's own health, and it closes itself
  when the run finishes. Dispatches that the journal describes stop looking observed when the
  monitor stops reading it. `docswarm_monitor` refuses `dispatch`, `phase`, `handoff` and
  `finish` on a swarm that has an executor journal, and `status` says who is running now. A
  swarm run earlier is replayed from its journal: the 40 dispatches of the Laya run, with
  their five refusals and their times, show up as they were. Open the monitor before `run`;
  it waits for the journal. See `docs/monitor.md`.
- The final report of an executor swarm lists what the executor had to redo. Its
  "Watchdog recoveries" section reads only the monitor's snapshots, so a run with a repair
  round, refused attempts, a restarted task or a withdrawn verdict was reported as "None
  recorded". A new **Executor rework** section, derived from `reports/execution/journal.jsonl`,
  lists each refused attempt (agent, attempt, first reason), repair round, task started
  twice, stale answer, withdrawn verdict, change of the approval grade or the cycle ceiling,
  plan recovery and plan change, with the cycle (taken from the task id when the event has
  none). A swarm the executor did not run has no such section, a run with nothing redone says
  so, unreadable lines are counted in the text, and a journal that cannot be read refuses the
  report. The narrative stage is left out on purpose: it is written after these facts and
  about them, and the engine reuses an accepted narrative only while those facts stay the
  same. Cells are one printable line each, cut and with their pipes escaped.
- The second real run (`docs/executor.md`, "Segunda execução real") ran the same brief again
  after the fixes. It was approved in cycle 4 under `A-` in 46.1 minutes without a person,
  against 68.8 minutes of working clock (and a 77.9 minute wait for a decision) in the first;
  it spent 0.3 % of the clock with nothing running against 61.8 %; the median call of the
  short roles was about half as long; and the cost was the same within the variation of two
  runs. It is two single runs with several changes between them, not a paired benchmark.

### Changed

- The grade a review has to reach is now declared by the review: `approval_grade: A-` or
  `A`, and `A` when the field is absent. By decision of the skill's owner on 2026-10-07,
  while the swarm's own excellence and performance are being worked on, a new executor
  swarm approves at `A-`. `gate.py` reads the value from the review, applies it to the
  topics and to the editorial surfaces, and records it in its result only when it differs
  from `A`, so everything written before the field existed still reproduces. Nothing below
  `A-` approves and a critical finding of the rubber duck still vetoes. The final report
  states the grade, so an approval at `A-` is never read as `A`. The executor takes it from
  `--approval-grade`, the brief's `approval_grade` or the current policy
  (`PROVISIONAL_APPROVAL_GRADE`, one line to revert), keeps it in the plan, journals a
  change as `approval_grade_changed`, and leaves a swarm that already has a plan under the
  grade it runs under. Judging a cycle that already exists under another grade pays only
  the audit of the new matrix and the narrative of the new outcome. The feedback to authors
  lists only what is below the grade. Presentations keep `A`. `SKILL.md` section 6, the
  README and `docs/executor.md` describe it.
- `verify_sources.py` checks URLs concurrently: 8 at a time and at most 3 per host
  by default (`--workers`, `--per-host`). The report keeps the index order and
  `--workers 1` is the sequential behaviour. Each address is isolated: a server that
  does not speak HTTP, a malformed port or a control character is one dead source, not an
  aborted run, and an address with accents is requested in its ASCII form (IDNA host,
  percent-encoded path and query) instead of being reported dead. It exits 3 when the
  check itself crashes.
- `verify_tables.py` treats a marker parameter the arithmetic cannot carry (`NaN`,
  infinity, a huge exponent, a target that is not a number) as a failed table the author
  can fix, and exits 3 when the check itself crashes.
- `gate.py` exits 3 on an internal error instead of leaving through Python's default exit
  status 1, which means "rejected".
- The prompt of an executor task embeds the JSON schema its answer must obey, so a
  backend without native schema enforcement still sees the contract.
- At most 9 authors are supported by the executor: each owns a range of one hundred
  source identifiers.

### Known limits

- Markdown only, and only for new swarms. Presentations, PDF and the evolution of a
  swarm that already has cycles keep the coordinator flow; the executor refuses them
  before spending anything.
- The `copilot` backend was qualified with real calls on 2026-10-07 (CLI 1.0.93-2,
  `gpt-5-mini`, 7 of 7 probes) and produced a whole article through four cycles (see
  `docs/executor.md`, "Primeira execução real"). It was qualified again after the MCP
  servers were stopped per task (CLI 1.0.93-4, 7 of 7, web probe included). It is not
  qualified on other CLI versions: run `qualify` after upgrading. A second real run
  followed (`docs/executor.md`, "Segunda execução real"), but no paired benchmark against
  the coordinator flow was run, so there is no promised percentage gain.
- A dead source found by the final recheck blocks the delivery until someone replaces it.
- The length a brief asks for is not checked by anything: the first real run delivered
  5,525 words against a requested 2,500 to 3,500, and the second 4,997.
- The saving from stopping the MCP servers was measured on a trivial call and then on a whole
  run, but the second run changed the CLI version, the grade and the panel at the same time,
  so it is not isolated. The listing of the servers is read whole or not at all, so a CLI
  whose listing changes format loses the saving (and nothing else) until the parser is taught
  the new format.
- An approved verdict is bound to the brief, the rubber duck's declaration, the document,
  the grades and the checks (through the identity of the audit), not to the declarations of
  the authors and reviewers, which the document and the grades already pin. A rejected cycle
  that already has a successor is history: only the current cycle is judged again when the
  grade changes.
- Only run on Windows. The `fcntl` lock and the process-group kill are the Linux and
  macOS paths and have never run; run the test suite there before relying on them.
  Files the executor writes on POSIX are owner-only (0600), a `mkstemp` default.
- The executor defends against what an agent returns, stale or interrupted state and
  accidental or by-hand edits. It does not defend against someone who controls the whole
  swarm folder: the journal and the records can be rewritten together, with no signature
  or hash chain, and `final_report.py` and `update_memory.py` trust `gate.py` and the
  matrix as they always did.
- Source addresses are screened without the network: a public name that resolves to an
  internal address, or that redirects to one, is not seen. This is deliberate, since
  refusing by resolution would also refuse legitimate internal sources.
- Agents with web tools run with `--allow-all-urls` (research cannot work otherwise) and
  receive the brief and the document in the prompt, so a page carrying malicious
  instructions could try to make them put that text in a URL. Path confinement stops
  reads of other files, not this. The coordinator flow has the equivalent exposure.

### Fixed

- Building the live feed of the monitor showed that the readers of the executor's journal
  trusted the type of every field of a file that is read back from disk, and that a hostile or
  merely odd entry stopped the reading of all the others. Fixed with a test and a mutant each:
  - `health.executor_state` and `metrics.executor_events` used the task id, the attempt, the
    stage and the cycle as dictionary keys. A list or an object where the engine writes a word
    raised `TypeError: unhashable type`, which ended `health.py` and `metrics` with a traceback.
    Both normalise those fields with `common.scalar`, and `metrics` no longer raises on a
    `seconds` that is not a number.
  - A journal line the JSON parser refuses for a reason other than its syntax (an integer of
    thousands of digits, a list nested a hundred thousand deep) raises `ValueError` or
    `RecursionError`, which neither reader treated as an unreadable line. `health.py` and the
    projection now skip and count it, like a torn append; `metrics` reports it as invalid JSON
    with its line number.
  - The journal reader is one function (`health.read_executor_journal`), shared by `health.py`
    and `executor_view.py`, so a fix to how a line is read cannot reach one and miss the other.
  - `common.scalar`, which makes a journal field fit to be a key, raised `RecursionError` for a
    value nested deeper than the JSON encoder follows and `ValueError` for one that contains
    itself. It now returns a marker string, so such an entry cannot end the reading of the others.
- `metrics` over a swarm whose monitor followed the executor printed a block with zero
  dispatches before the right one: the monitor's journal of such a swarm holds only
  `executor_batch` events, none of the vocabulary `metrics` reads. A journal with nothing of
  its own dispatched is no longer measured as a run (the executor's journal is), its name is
  reported on stderr, and the id the monitor shows selects the executor's figures in
  `--execution`. What the monitor recorded itself is still measured, and a test ties the event
  type the extension records to the one `metrics` skips.
- Two independent reviews of the changes that followed the first real run (one of the
  design, one line by line) found, and the following were fixed with a test and a mutant:
  - `task_started` and `task_recorded` did not carry the identity of the task
    (`inputs_sha256`), only `task_issued` did. A result accepted for earlier inputs could
    therefore vouch for the file of a task asked again with the same id and attempt,
    `status` counted the new issue as the old one, and `health.py` hid the pending work of
    one identity behind the answer to another. All three events carry it and every reader
    matches on it, tolerating a journal written before (an entry without it covers
    whatever was asked, as it always did).
  - A review could declare `approval_grade: A-` by itself, and a `plan.json` that was
    deleted or edited made the swarm start over under the policy of a new one (see Added).
  - The monitor drew an `A-` approved by the gate as a failure, because `A` was written in
    three places of the panel and in its page (see Added).
  - A cancellation could arrive while the MCP servers were being listed, between the check
    for a cancellation and the registration of the process: an agent then started after
    Ctrl+C, ran unwatched for up to its timeout and had its paid answer discarded. The check
    and the registration are now one step. The listing is a supervised process too:
    `cancel()` reaches it, and one that overruns its 60 s is ended with everything it
    started (it used to wait for children holding the pipes open).
  - A listing of the MCP servers with a line that is neither a heading nor an entry was used
    up to that line. The builtin servers are stopped by one flag for all of them, so a cut
    listing could stop one the task needs. The listing is now read whole or not at all.
  - A reply that could not be read, holding a lone surrogate, made `record` raise
    `UnicodeEncodeError` while writing the excerpt kept for diagnosis: the attempt was not
    counted and the same agent was paid for again. The excerpts are made safe to write.
  - The search for code fences in a reply was quadratic: 144 KB of opening fences took 25 s
    with the swarm locked. It is one pass now, and a test compares it with the regex it
    replaced on thousands of generated strings.
  - Every reader of `plan.json` now goes through one accessor that tolerates a plan or an
    `options` that is not an object, where one of them raised an unhandled exception.
  - `metrics` has a test for the run of a re-issued task that never ended: the record that
    exists belongs to the first run and must not hide that the second has none.
  - A test now pins that an approved verdict does not survive an edit of the brief or of the
    rubber duck's declaration (it is bound through the identity of the audit), and that the
    declarations of authors and reviewers are deliberately not part of it.
- First real run of the executor (an article about the Laya model, four cycles):
  - The contract was enforced but not stated. A coordinator was refused twice for
    writing "author-01 e author-03" and "T01 / T06 (licença)" where the engine needs one
    exact author name and one topic id, and a reviewer was refused for writing
    "T01: título" where it needs "T01". The schemas now enumerate the valid values
    (`divergences[].topic` and `.author`, the reviewer's `topic`), the prompts name them,
    and each refusal lists what is accepted. The same was done for the limits no prompt
    stated: source title and type length, the public-URL rule, the narrative length, the
    number, size and path length of an author's files, and the document size.
  - The rubber duck was shown the matrix with the executor's own marker for "audit not
    recorded", which fails the gate closed by default, read it as a critical defect and
    vetoed a cycle. It now sees the matrix without the `rubberduck` section, and its
    prompt says it does not decide approval.
  - "Raise the ceiling and run again" paid the last cycle again, because the brief's text
    is part of every task's identity (measured on a copy: the engine withdrew the cycle 4
    verdict and re-issued its three authors, even for a change of `monitor: false` to
    `true`). `--max-cycles` is the supported way (see Added).
  - A reply that could not be read was refused and left nothing to diagnose (an author after
    218 s with 1,088 characters, a narrative of 4,540). The parser now accepts the line breaks
    and tabs a model leaves unescaped inside a long string, the refusal says where the JSON
    breaks (including a reply cut off before its last brace), and the record of the attempt
    keeps the head and tail of the reply (300 characters each; not the journal).
  - A task asked again under a new identity (same id and attempt, other inputs) reused the
    earlier issue in the journal, so its record was measured from it (4,850 s for a one-minute
    audit) and `metrics` saw dispatches that never ended. The issue is now journaled per
    identity. The usage record of that second call no longer overwrites the first: a later
    call with the same label is written beside it (`<label>.2.json`).
- `resume.py` no longer asks a presentation swarm to "compose" forever. It looked for
  `output/*.md`, which a presentation never has, so every watchdog tick projected a
  recomposition: one recorded run took five R5 recoveries and rebuilt the deck three
  times (`presentation-cycle-03-r2`, `-r3`, `-r4`). The delivery is now the list the
  brief declares in `deliverables`, validated like the gate validates it, and each
  declared file is hash-bound evidence of the projection. Swarms that declare none keep
  the original Markdown lookup.
- Preserve native Space activation on focused presentation controls, scope the
  position counter to support pages, and prevent delayed dialog-close events
  from undoing subsequent navigation.
- Keep disabled HTML controls fully opaque and add alternative descriptions to
  saved PowerPoint controls and the faithful deck's page images.
- Exercise keyboard activation against a non-adjacent index destination, with
  deliberately broken keyboard and support-counter controls in regression tests.

## [3.5.0] - 2026-10-05

### Added

- Execution health measured outside the agent loop: the monitor extension tracks
  the age of the newest observation and publishes `health.json` per execution,
  so a wedged session that keeps reporting `processing` is still seen as stalled.
- `health.py`: stdlib health table composed from artifacts, the resume projection
  and the extension record; prints "not observed" instead of a plausible value.
- `resume.py`: stdlib projection of the next deterministic step of the cycle
  contract plus a durable `reports/resume.json` bound to artifact hashes, so a
  fresh session can continue and a changed artifact forces a recomputation.
- Watchdog protocol in `SKILL.md` section 2.6: a five-minute scheduled prompt
  that prints the table, a recovery catalogue R1 to R5, forbidden actions and
  ceilings of one action per tick, two per agent per cycle and six per execution.
- `docswarm_monitor` operation `recovery`, journaled per event and capped by the
  extension, plus a recovery section in `final-report.md` so a resumed execution
  never looks clean.
- Stall and lost-observation indicators in the monitor panel.

### Changed

- The health classification no longer trusts the session label over the
  measurement: `processing` beyond the threshold with no running agent is
  reported as stalled, and a running agent earns grace only up to three
  thresholds.
- A health file the extension stopped refreshing is discarded in favour of
  artifact ages, and the report says so.
- The monitor's internal read failure channel was renamed from `health_error`
  to `reader_error`, to separate it from execution health.

## [3.4.0] - 2026-10-05

### Added

- Optional presentation delivery: one authored DeckSpec produces offline
  interactive HTML, an image-faithful PowerPoint file and an editable PowerPoint
  file with native title, text, tables, shapes and anchored connectors.
- Strict JSON deck schema with a stdlib schema subset validator, a redistributable
  neutral theme and standard-library TrueType metrics shared by all three outputs.
- One resolved composition plan: pages, lines, reading order, support pagination
  per origin and a reconstructed navigation graph including disabled extremes.
- Offline HTML runtime with a strict content policy, no inline styles, modal
  support dialogs, focus return, keyboard sequence and separate presenter notes.
- Artifact inspection of the saved files: delivered markup, OOXML inventory,
  notes, hidden support slides, anchored connectors, stage bounds, package safety
  and real browser interaction in a pinned Chromium.
- Environment qualification that measures the installed PowerPoint, rehearses
  edit/save/reopen on copies and calibrates the visual difference per page.
- `presentation_contract.py`: stdlib gate validation that rebuilds pages,
  materialisations, navigation and the review domain from the deck itself.
- `preflight`, `build`, `inspect` and `publish` operations plus acceptance
  recording and an all-or-nothing promotion to a new destination.

### Changed

- `artifact_type: presentation` selects the new delivery; absent it, historical
  entries stay documental and legacy slide fields keep their original meaning.
- Presentation reviews cover a closed set of dimensions per topic, per page and
  per format; none of them accepts `not_applicable`, and `editorial-v1` still
  applies to the full delivered text.
- Non-terminal inspection states (`pending`, `not_evaluated`, `unsupported`,
  `stale`) never approve: the gate returns 3 instead of a grade.
- The document path is unchanged and gains no mandatory dependency; the
  presentation libraries stay outside `scripts/checks`.

## [3.3.0] - 2026-10-05

### Added

- Optional portable Markdown-to-PDF engine using ReportLab/Platypus, with textbook
  and technical-report profiles sharing a common visual identity.
- Original vector cover artwork, justified flowing text, navigable contents,
  multipage tables, literal code, Unicode formulas and source-authored vector figures.
- Bundled unmodified OFL fonts, their licenses and pinned upstream hash manifest.
- Artifact-based PDFium inspection of text/operator fidelity, glyph bounds,
  raster visibility, table/figure layout, links, navigation and font embedding.
- PDF/PNG/inspection/manifest bundles and stdlib gate validation of their hashes.
- `docswarm_pdf` render/inspect extension and opt-in personal installation.
- Reference fixtures and deliberately damaged PDFs covering text/operator loss,
  missing pages, invisible text, margin overflow and erased vector ink.

### Changed

- New PDF deliveries are composed and mechanically inspected before full
  editorial/visual review. The engine neither rewrites source content nor grades it.
- PDF dependencies remain optional and outside `scripts/checks`; existing
  pre-3.3 historical documents keep their original validation contract.
- Installation examples, workflow diagrams and output trees include the optional
  PDF bundle and the current inspection/review order.
- Git preserves original font and license bytes, preventing platform line-ending
  conversion from invalidating the pinned artifact hashes.

## [3.2.2] - 2026-10-02

### Changed

- Public documents prefer descriptive names; required acronyms and local codes
  need first-use definitions, consistent meanings and self-contained visual legends.
- Internal swarm IDs remain in control artifacts. Renderers consume approved
  labels instead of inventing abbreviations during composition.
- Clarity review and rubber-duck audits explicitly cover opaque codes, late
  definitions, conflicting meanings and unsupported claims of market standards.
- Declaration templates and existing-declaration guidance include nomenclature
  duties without adding agents or changing the `editorial-v1` data contract.
- Declaration linting rejects duplicate frontmatter keys instead of silently
  allowing a stale guidance version or model field to override the intended value.

### Added

- Stdlib `inspect_nomenclature.py` reports lexical candidates and source locations
  without guessing definitions, assigning grades or claiming complete coverage.
- Nine annotated nomenclature calibration cases, including correctly defined
  local codes, and scanner/reader/editorial regression coverage.

## [3.2.1] - 2026-10-02

### Changed

- Editorial justifications now explicitly address wording, referents, tone and
  standalone meaning, independently of technical correctness or decision quality.
- Authoring guidance converts internal directions into stated application
  conditions, limitations and consequences without inventing customer requirements.
- Visual evolutions preserve facts, logic, scope and caveats while permitting
  wording corrections, followed by renewed review and artifact binding.
- Rubber-duck audits compare grades with passages and justifications and return
  unsupported editorial approvals to the responsible reviewer without silently
  replacing the grade.
- Declaration templates and profile-reuse metadata carry the guidance version;
  existing declarations must be updated explicitly.

### Added

- Annotated calibration pairs across titles, openings, prose, cards, captions and
  conclusions, including a technically valid shared-AKS recommendation with
  unsuitable wording and an acceptable negative statement.
- Regression cases distinguish annotated editorial judgments from mechanical
  schema/hash validation. The `editorial-v1` data contract is unchanged.

## [3.2.0] - 2026-10-02

### Added

- Explicit `editorial-v1` review contract for new documents and evolutions,
  covering titles, openings, prose, captions and conclusions.
- Current-cycle, full-document editorial assessments with concrete quotations,
  independent wording grades and unresolved-finding vetoes.
- Delivery/text hash verification and agreement with the assigned reviewer's
  original JSON before approval, final reporting or profile reuse.
- Annotated generic/specific wording fixtures and end-to-end editorial gate tests.

### Changed

- Authors and coordinators must keep editorial wording in the source document;
  renderers cannot introduce unreviewed slogans, headings or callouts.
- Visual quality and historical approval no longer substitute for the current
  editorial assessment. Targeted/layout-only editorial reviews are invalid.
- Monitor, final report and memory eligibility share current-artifact validation.
- Historical pre-contract reviews keep their original semantics; existing PDFs
  and prior grades are not silently rewritten.

The checks validate review records and artifact identity, not AI authorship or
writing quality inferred from a blacklist. Semantic judgment remains explicit.

## [3.1.0] - 2026-10-01

### Added

- Read-only local swarm monitor with agent graph, live runtime states, cycles,
  per-reviewer scores, artifact details and execution history.
- Compact 720x480 default panel with a short indicator strip, accessible tabs,
  in-panel scrolling and an expand/compact control.
- DPI-aware fitting of the native Copilot canvas window on Windows, bound to the
  host process and a unique execution title. Expansion/compaction resize only
  that window; ordinary browser tabs retain host-managed sizing.
- Copilot canvas extension with automatic local-browser fallback, explicit task
  correlation, native lifecycle observation and task-state reconciliation.
- Versioned per-execution journals and atomic snapshots, with replay, writer
  ownership and stale-observation handling.
- Structured individual reviewer artifacts and a stdlib artifact projection reader.
- Optional `gate.py --output` records tied to the exact review bytes.
- Native Node, Python, installer and isolated demonstration fixtures.

### Changed

- The skill publishes actual coordinator milestones and registers dispatches
  before invoking the real task tools. The monitor never controls agents.
- Installers register the personal extension by default, with document-only
  opt-out, managed-link preflight and non-destructive collision handling.
- Personal registration yields to a project copy to avoid duplicate global tool
  names when both extension sources are loaded.
- Idle agents are labeled as available rather than implying a user response.
  Principal-session activity is observed separately, follow-up correlation uses
  native task/turn identities, and new finish requests wait for `session.idle`
  instead of freezing observation before the runtime settles.
- Python-to-Node transport explicitly preserves UTF-8. Machine-readable JSON
  output is encoding-independent; files retain readable UTF-8 text.
- Source documents, approvals and historical deliverables remain authoritative;
  unavailable monitoring never relaxes the document quality gate.

## [3.0.0] - 2026-10-01

### Removed

- Presentation creation and evolution, PPTX triggers, slide-author and deck-builder
  templates, content/design reviewer variants, rendering workflow and deck example.
- Presentation toolchain checks and installation from both installers, including
  `-WithPresentation` and `--with-presentation`.

### Changed

- The skill now creates and evolves documents only; the Principal Cloud Solution
  Architect editorial profile and document quality workflow remain in place.
- Document final reports omit slide and deck sections when the review has no such
  fields. Historical deck reviews retain their original grades and blocking rules.
- Unsupported installer arguments fail before installation instead of being ignored.
- Existing deliverables, reports and historical changelog entries are preserved;
  previously installed presentation tools are not uninstalled.

## [2.1.0] - 2026-10-01

### Added

- Shared Principal Cloud Solution Architect editorial profile for cloud
  architecture documents and presentations, with audience-specific depth.
- Explicit editorial profile in new briefs and role-specific contracts in
  declarative agents, including reused profiles.

### Changed

- Existing authors apply the profile; clarity, decision-completeness and factual
  reviewers assess their own dimensions without rewriting the content.
- Editorial criteria feed the existing topic grades and quality gate instead of
  introducing a separate editor, agent or approval stage.
- Coordinators align voice and terminology without inventing facts or silently
  resolving technical disagreements.
- Slide authors preserve decision context and visible conditions; deck builders
  preserve meaning, sources, caveats and speaker notes from the specs.
- Instruction-only updates preserve historical deliverables and approvals;
  applying the profile to existing content requires the evolution workflow.
- README and document/deck examples describe the new editorial responsibilities.

## [2.0.0] - 2026-07-13

### Added

- Deterministic source verification with cache and auditable JSON.
- Deterministic arithmetic checks for marked Markdown tables.
- Structured review YAML and an executable quality gate for documents and decks.
- Generated factual skeleton for `final-report.md`.
- Agent frontmatter linting and session model provenance rules.
- Curated cross-swarm memory with proposal-first updates.
- Skill version stamps in briefs and final reports.
- Python stdlib test suite and real arithmetic regression fixtures.

### Changed

- Evidence requirements are now proportional to the reviewer role.
- The cycle order now runs source and table checks before reviewers.
- The final decision follows `gate.py` exit codes instead of prose interpretation.
- Escalation now generates the deterministic final report before notifying the user.
- Unattached table-check markers fail instead of silently disabling arithmetic checks.
- README is an entry point; `SKILL.md` is the sole behavioral specification.
- Repository paths use `/` except in Windows-specific commands.

## [1.2.0] - 2026-07-07

### Added

- PowerPoint presentation mode with slide authors, deck builder, content review,
  multimodal design review, native rendering and speaker notes.
- Optional presentation toolchain installation.

## [1.1.0] - 2026-06-29

### Added

- Explicit model selection, reasoning effort, context tier and rationale per
  declarative agent.
- Document evolution workflow and worked CoE de Nuvem example.

## [1.0.0] - 2026-06-29

### Added

- Initial standalone Document Swarm skill with authors, reviewers, coordinator,
  rubber duck, evidence tracking and the `A` quality gate.
