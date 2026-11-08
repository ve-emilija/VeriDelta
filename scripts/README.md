# Verification scripts (not part of the package, not run by any test)

Every script here was written and run during the `v0.3-change-aware-edges-evidence`
(bind index / component edges / evidence citations / git-diff method-body change detection) work, to check
real behavior against real checkouts before anything in `src/veridelta/` was trusted. They are committed
per an explicit request — "provide scripts and everything you used" — so the verification record is
reproducible from the repo, not only described in `docs/change_summary_validation.md`.

**Why hand-written, fixture-free scripts exist alongside `tests/`:** this sandbox has no working
`pip`/`pytest` install. `tests/` is written as ordinary `pytest` (and should be run with real pytest
wherever that's available — e.g. CI, or your own machine). Inside this sandbox, two things stood in for
it:

1. **`stub/`** — a ~30-line stand-in `pytest` shim (`pytest.py`, supports only `mark.parametrize` and
   `importorskip`) plus a runner (`run_all.py`) that discovers `tests/test_*.py`, imports each with the
   stub on `sys.path`, and runs every `test_*` function. It does **not** support fixtures (`tmp_path`,
   custom fixtures like `conftest.py`'s `adjacency`/`node_types`/`history`/`paper_data`, or the `repo`
   fixture in `test_git_diff.py`) — those fail under it with `TypeError: missing N required positional
   arguments`. That's a known limitation of this 30-line shim, not a bug in the tests themselves; run the
   real suite with real `pytest` to exercise fixture-based tests.
   ```
   python3 scripts/stub/run_all.py /path/to/veridelta/checkout [optional substring to filter test files]
   ```
2. **The `manual_*_check.py` / `validate_*.py` scripts below** — fixture-free, hand-written verification
   harnesses for exactly the cases the stub can't run (anything needing a real git repo, a real OpenTitan/
   Caliptra-rtl checkout, or the CLI/pipeline end-to-end). Each prints `[OK]`/`[FAIL]` per check and exits
   non-zero on any failure.

## What each script checks

- **`manual_cli_check.py`** — 21 checks that `cli.py`'s `build-graph`/`blast-radius`/`analyze` commands
  correctly wire the bind-index mechanism end to end (writing `bind_index.json`, the `--rtl-module` and
  `--show-associations` flags) — this was previously a completely disconnected mechanism; see
  `docs/change_summary_validation.md` §3.
- **`manual_predict_check.py`** — 45 checks on `graphrag_pipeline.predict()`'s evidence citations (every
  prediction gets a `graph_path` + `cited_commits` dict) and the `depth`-carrying change in
  `merge_predictions()`.
- **`validate_component_edges.py`** — runs `component_edges.py`'s edge extraction over the real,
  already-cloned OpenTitan (`hw/`) and Caliptra-rtl checkouts and prints the real per-edge-kind counts
  reported in `docs/change_summary_validation.md` §2 (requires those checkouts at
  `/home/claude/sources/opentitan` and `/home/claude/sources/caliptra-rtl`; without them, it's a record of
  what was run, not a script you need to re-run to trust the numbers already written down).
- **`manual_git_diff_check.py`** — 23 fixture-free checks on `git_diff.py`'s new method-body-change
  detection (`parse_unified_diff_ranges`, `method_body_diff`, and `class_change_summary`'s wiring of
  `methods_body_changed`), each against a real, synthetic git repo built with real `git` subprocess calls
  in a fresh temp directory — deterministic, no network access needed. Run with `python3
  scripts/manual_git_diff_check.py`.
- **`validate_git_diff_opentitan.py`** — re-runs the exact 9-case validation table from
  `docs/change_summary_validation.md` §1 against the real OpenTitan checkout, printing has-signal
  before/after the method-body-diff feature for each case — this produced the "3/9 → 1/9 zero-signal
  cases" number in that doc. Requires the OpenTitan checkout at `/home/claude/sources/opentitan` with the
  9 cited commits fetched locally (`git fetch --depth 2 origin <full-sha>` for each, if starting from a
  shallower clone).

None of this is imported by `src/veridelta/` or by `tests/` — it is a standalone record, kept here instead
of deleted once the features it checked were trusted, because "what did you actually run" is itself part
of what a paper reviewer or a future contributor needs to be able to check.
