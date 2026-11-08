# Roadmap

## Done (`v0.3-change-aware-edges-evidence`, 2026-11-08)

- **Facts from the diff in the prompt** (was #2): `change_summary.py`/`git_diff.py` build a compact
  structural change summary (members/methods/constraints/macros/curated API-call deltas) and feed it into
  Stage 3 via `build_prompt(..., change_summary=...)`. Validated on 7 real OpenTitan commits, 6 real bugs
  found and fixed, 3395-class corpus-wide performance pass. **Not yet wired into the 400-commit
  evaluation** — the measured effect on accuracy is still open. See `docs/change_summary_validation.md`.
- **Assertion Orphaning plumbing** (was #6, partially): `bind_index.json` is now built by `build-graph` and
  reachable from the CLI (`--rtl-module` on `blast-radius`/`analyze`) — it previously existed in
  `type_annotator.py`/`graphrag_pipeline.py` but was completely disconnected (hardcoded to `{}`/`[]`
  everywhere). Still informational only: "Assertion Orphaning" is not in `VALID_CATEGORIES`, so it is not
  scored. Adding it as a 6th category needs new human labels (see "Open" below).
- **The missing edges** (new, not on the original list): member handles, `uvm_config_db` set/get,
  analysis-port `.connect()`, and factory overrides are now extracted (`component_edges.py`) and surfaced
  (`--show-associations`). Validated against the full OpenTitan + Caliptra-rtl checkouts, 3 real bugs found
  and fixed. Not merged into the scored blast radius — informational only, same reasoning as above.
- **Evidence citations**: every prediction now cites its graph path (inheritance depth or bind index) and
  the specific past commits that changed that same node — not a blanket list of everything Stage 2
  retrieved.

## Open

1. **History builder for any repository.** `veridelta build-history`: read `git log`, link changed `.sv/.svh`
   files to graph classes, derive an IP block from a configurable path rule. Today the history file exists only for
   OpenTitan and Caliptra (built by `experiments/paper/prepare_data.py`).
2. **Measure the change-aware prompt's effect** on the 400-commit evaluation (needs file paths the sample
   data doesn't store today, or an API-costing re-run) — and separately, **measure merging association
   edges into the scored blast radius** the same way.
3. **"Prior plus evidence" mode.** Start from the structural prior and let the model only add categories it can
   support with evidence from history or the diff.
4. **More projects** (CORE-V, other RISC-V cores) and a naming-convention profile per project, so the type function
   is not tied to lowRISC suffixes.
5. **Ground truth from real regression failures** instead of model-labeled commits.
6. **Extend the human-labeled sample** from ~20 to ~100 commits, always reporting against the no-graph
   baseline (already true today: `llm_no_graph` is one of the systems `run_eval.py` always evaluates).
7. **A 6th category, Assertion Orphaning / Coverage Regression from bind**, scored properly — needs the
   label extension in #6 first.
8. **Integration / change notification:** a GitHub Action / CI step that comments the blast radius and
   likely failure modes on a pull request, citing the evidence block from the prediction. Design sketched
   in `docs/change_summary_validation.md` §6 (deliberately not built — paper-4 scope); open questions there
   include confidence gating and PR-comment vs. inline-annotation delivery.
9. **Reconsider the RAL-generated-boilerplate noise** in member-handle edges before merging association
   edges into scoring (see `docs/change_summary_validation.md` §2/§5) — a change to a widely-used RAL base
   pattern currently produces a disproportionately large, low-value blast radius via these edges.
10. **Project hygiene:** lint and type checks in CI, a PyPI release, a larger human-checked sample.
