# Validation log: change-aware prompts, component-coupling edges, bind index, evidence citations

This is the paper-facing record of the November 2026 roadmap work (`v0.3-change-aware-edges-evidence`
branch): what was built, what it was validated against, every bug that validation found and how it was
fixed, and — honestly, not glossed over — what still does not work. Each claim below is reproducible from
the commits on that branch; nothing here is reported from memory.

## 1. Change-aware Stage 3 prompts (`change_summary.py`, `git_diff.py`)

**What it does.** Before this, Stage 3 told the model *which* class changed and *what is downstream of
it*, never *what changed inside the class*. `change_summary.py` builds a compact structural diff from two
independently-extracted class bodies (no unified-diff parsing): members / methods / constraints / macros
added, removed, retyped or signature-changed, plus the net change in calls to five curated API groups
(RAL/CSR, `uvm_config_db`, analysis-port connect/write, factory create/override, `randomize`).
`git_diff.py` is the (separately testable) glue that pulls the two revisions of a file out of a real git
checkout and feeds them in.

**What it is seen against.** 7 real OpenTitan commits, chosen to span the IP blocks and change shapes
already used in the paper's qualitative examples: `a6241327` (dv_base_driver), `5f6efdee` (kmac_scoreboard
/ kmac_env_cfg / kmac_base_vseq), `586d24e1`, `0cd8865c` (aon_timer_base_vseq / aon_timer_env_cov),
`06367c42` (aes_base_vseq), `2eb7256b` (dv_base_env), `da7fc357` (lc_ctrl_base_vseq) — plus their parents,
fetched with `git fetch --depth 2 origin <full-sha>` (enough to diff `sha~1` against `sha` without a full
clone). 9/9 of the resulting before/after class-body comparisons on these commits were correct once the
bugs below were fixed.

**Bugs found and fixed, in the order found:**

| # | Bug | Real case | Fix |
|---|---|---|---|
| 1 | Local variable inside a method misread as a removed class member | `2eb7256b`, `dv_base_env.sv`: `string default_ral_name;` inside `build_phase()` | blank task/function bodies (same character length, so offsets stay valid) before member/constraint extraction |
| 2 | Catastrophic/quadratic regex hang (~10s+) | `kmac_scoreboard.sv`, 1700+ lines, after bug 1's blanking produced long whitespace runs | matched member declarations per physical line instead of `^\s*` over the whole body with `re.MULTILINE` |
| 3 | Member with an initializer missed entirely | `5f6efdee`, `kmac_env_cfg.sv`: `bit enable_full_kmac = 1;` | added an optional `(?:\s*=\s*[^;]*)?` initializer clause |
| 4 | Packed-dimension declarations missed entirely | found while debugging a test failure (not in the original 9-commit set — could have been silently wrong corpus-wide) | added the packed-dim group to the type pattern |
| 5 | `has_any_signal()` always returned `True` | iterated every dict key including metadata (`class_name`), which is always a non-empty string | explicit `_SIGNAL_LIST_FIELDS` whitelist |
| 6 | Array-dimension changes (`int a;` → `int a[4];`) not flagged as retyped | test design | capture the dimension and fold it into the stored type identity |

**Corpus-wide robustness pass** (after the fixes above): every class in the OpenTitan + Caliptra-rtl
checkouts compared against itself (3395 classes), 0 slow cases, ~4.46 ms/class average. This is a
self-diff sanity check (no real change to detect), not a correctness check on real diffs — the 9-commit
set above is the correctness evidence; this is the performance/crash evidence.

**Update, 2026-11-08 — the method-body gap above is now closed for real, because paper reviewers flagged
it as a major point.** `change_summary.py` gained `method_line_spans()` (a method's absolute, file-relative
line range — a pure function, no git access, consistent with the rest of the module); `git_diff.py` gained
`parse_unified_diff_ranges()` (turns a `git diff --unified=0` hunk header into the set of line numbers it
covers) and `method_body_diff()` (intersects a method's line span, on BOTH revisions, against the real
diff's touched lines). `class_change_summary()` now calls this automatically and fills in
`methods_body_changed`, excluding any method already explained by `methods_added`/`methods_removed`
(a rename looks like exactly this kind of line churn and must not be double-reported). `render_change_summary()`
surfaces it as a new line, *"method bodies touched (diff, signature unchanged)"*.

**Re-validated against the same 9 real cases** (see `scripts/validate_git_diff_opentitan.py` — same repo,
same commits, same classes as the original 9-case table above): zero-signal cases dropped from **3/9 to
1/9**. Two cases that previously had *no* member/method/constraint/call-delta signal at all now correctly
report real method-body changes:

| Commit | Class | `methods_body_changed` |
|---|---|---|
| `a62413272` | `dv_base_driver` | `get_and_drive`, `reset_signals`, `run_phase` |
| `5f6efdee7` | `kmac_scoreboard` | `get_digest_len_and_xof`, `incr_and_predict_hash_cnt`, `process_kmac_app_fsm`, `process_tl_access` |

The remaining zero-signal case, `0cd8865c3` / `aon_timer_base_vseq`, is a **new, separate, honestly-documented
gap**: that class declares its methods with SystemVerilog's `extern task foo(...);` / `extern function ...;`
syntax and defines the actual bodies *outside* the class, as `task aon_timer_base_vseq::foo(...); ... endtask`.
`extract_class_body()` (and so `method_line_spans()`, which is built on it) only sees text between the
class's own `class ... endclass`, so an out-of-line body's edits are invisible to `method_body_diff()` just
as they already were to every other signal in this module — this is not a regression, it is the same
class-body-only scope this module has always had, now visible in one more concrete case. Not attempted here:
matching `ClassName::method_name(...)` out-of-line definitions at the file level (outside any `class`
block) would need its own extraction pass, since these have no enclosing class body to extract at all.

**Tests:** `tests/test_git_diff.py` (new — 10 cases, each against a real, synthetic git repo created with
real `git` subprocess calls in a temp directory, so it is deterministic and needs no network access) plus
3 new cases in `tests/test_change_summary.py` for `method_line_spans()` itself. All pass under both the
sandbox's stand-in runner (for the fixture-free `change_summary.py` cases) and a dedicated fixture-free
manual harness, `scripts/manual_git_diff_check.py`, written because this sandbox has no real pytest
install and `test_git_diff.py`'s `pytest` fixtures (`tmp_path`) do not run under the stand-in shim — see
`scripts/README.md`.

*(Original note, now superseded by the above):* ~~a method **body** edited without changing its name or
argument list ... is invisible unless it happens to touch one of the five curated API-call groups ...
A future version that diffs method bodies line-by-line would close this; not attempted here.~~

**Not wired into the 400-commit evaluation.** `experiments/paper/run_eval.py`'s sample data
(`sample400.json`) stores only changed class *names*, not file paths, so building a `change_summary` per
evaluation unit needs either a git-grep-based file lookup for all 400 samples or re-running the pipeline
(API cost). Neither was done — by instruction, the cost/token-savings angle is deferred to a future paper.
The CLI path (`analyze --mock` or with a real key) **can** use `change_summary`/`git_diff` today via
`graphrag_pipeline.predict()`'s `change_summary=` parameter; the measured *effect on accuracy* has not been
run.

## 2. Component-coupling edges (`component_edges.py`)

**What it does.** Four edge kinds beyond class inheritance (which the graph already had): member handles,
`uvm_config_db` set/get, analysis-port `.connect()`, and factory overrides. `bind` statements (the fifth
item on the roadmap) were a *pre-existing, disconnected* mechanism
(`type_annotator.build_bind_index`/`graphrag_pipeline.get_bind_at_risk`/`bind_category`) — see §3.

Edge direction follows the same convention as the existing inheritance adjacency
(`adjacency[parent] = [children]`, so BFS from a change walks parent → child): `held_type → holder` for
member handles, `setter → getter` for config_db, `producer → consumer` for analysis-port, `orig → new` for
factory overrides. The first draft had member-handle direction backwards; caught before anything depended
on it (see the bug table below).

**Validated against:** the full OpenTitan (`hw/`, 4043 files with `--scan-all`, 2208 with the default
UVM-keyword filter) and Caliptra-rtl (1492 files) checkouts already cloned for this project. 24 new unit
tests on synthetic fixtures, then a corpus-wide pass with real counts and a manual spot-check of samples
from each edge kind.

**Bugs found and fixed:**

| # | Bug | Real case | Fix |
|---|---|---|---|
| 1 | member-handle edge direction was backwards (holder→held instead of held→holder) | caught in design review before any real data was run | flipped; documented the BFS convention on `ComponentEdge` itself |
| 2 | `uvm_reg_field` and other UVM-library base classes gained thousands of near-identical, zero-signal edges | Caliptra-rtl: auto-generated RAL register classes (`aes_clp_reg__AES_NAME extends uvm_reg`) each declare a `uvm_reg_field` member per CSR field — raw count before filtering: 1837 member_handle edges in Caliptra-rtl, of which ~1046 were `-> uvm_reg_field` | added `_LIBRARY_BASE_CLASSES`, a short denylist of UVM/OVM base classes, applied as a target-side filter on member_handle and analysis_port edges |
| 3 | `factory_override_edges` had **no** `known_classes` check at all (the other three kinds all had one) | OpenTitan: `csr_base_seq::type_id::set_type_override(csr_hw_reset_seq::get_type())` found in a file matched into the corpus, but `csr_base_seq` is not itself a node in `node_types.json` — produced an edge `blast-radius --show-associations` could never resolve | added the same `known_classes` filter the other three kinds already had |

**Final validated counts** (default `build-graph` file set, i.e. the UVM-keyword filename filter, not
`--scan-all` — this is what a real `veridelta build-graph hw/ --out ...` run produces):

| Repo | member_handle | analysis_port | factory_override | config_db |
|---|---|---|---|---|
| OpenTitan | 2116 | 15 | 2 | 2 |
| Caliptra-rtl | 641 (still mostly generated RAL register-model classes; see below) | 0 | 1 | 0 |

**Documented, not fixed — honest gaps:**

- **Inherited members are invisible.** This module only sees a class's *own* member declarations, not its
  ancestors'. Real case: `otp_ctrl_env.sv` (OpenTitan) reads `scoreboard.otbn_fifo.analysis_export` in
  `connect_phase()`, but `scoreboard` is declared on the parameterized base class `cip_base_env`
  (`SCOREBOARD_T`), not on `otp_ctrl_env` itself, so this specific `.connect()` call cannot resolve. The
  base class's own member-handle edge still exists (so the miss is partial, not total — the existing
  inheritance BFS still reaches `otp_ctrl_env` from `cip_base_env`), but the edge is attributed to the base
  class, not the concrete env. This is why only 24 (scan-all corpus figure before the library filter; 15
  with the default file set) analysis-port edges were found despite dozens of `.connect()` calls grepped
  directly from the source — most producer/consumer symbols on real `.connect()` calls are inherited, not
  locally declared.
- **Parameterized member types with nested parentheses break the member regex entirely**, not just the
  type portion. Real case: `otp_ctrl_env.sv`: `push_pull_agent#(.DeviceDataWidth(OTBN_DATA_SIZE))
  m_otbn_pull_agent;` — the nested `(...)` inside `#(...)` defeats `_MEMBER_DECL`'s own paren matching (the
  same limitation `change_summary.py` already has, since `member_handle_edges` reuses its `_members()`).
- **Auto-generated RAL register-model classes dominate the member_handle count**, especially in
  Caliptra-rtl: register-field-of-register-block edges (e.g. `aes_clp_reg__global_intr_en_t ->
  aes_clp_reg__intr_block_t`) are structurally real but low-value for change-impact — they are generated
  boilerplate, not hand-authored verification logic that could hide a bug the way env/scoreboard/agent
  coupling can. The `_LIBRARY_BASE_CLASSES` filter removes edges *to* the UVM library itself but not this
  class of generated-vs-generated edges; a future refinement could detect generated files (by a
  generator-marker comment or file-path convention) and exclude them, or down-weight RAL-typed node pairs.
  Not attempted here — a speculative filter without its own validation pass would trade one unverified
  number for another.
- **`uvm_config_db` matching ignores the `scope` string entirely** — UVM's actual resolution is
  hierarchical wildcard path matching against the real instance tree, which a text-only pass cannot
  elaborate. Matching on `(type, field)` alone is a conservative *over*-approximation (can link a setter and
  getter that would never actually resolve to each other with real instance paths); only 2–21 edges were
  found in either corpus, so in practice this has not produced a large false-positive set, but it has not
  been checked against the real RAL hierarchy either.

**Not merged into the scored Stage 1 blast radius.** `association_edges.json` is written by `build-graph`
and surfaced by `blast-radius --show-associations` / `analyze`'s text output, but it is **not** merged
into `adjacency`/the BFS that produces the blast radius `analyze` scores against. Doing so would silently
change the already-measured Table numbers without a deliberate before/after evaluation of the effect —
the same reasoning as change_summary.py not being wired into `run_eval.py` (§1). This is the natural next
step, explicitly left for a future pass.

## 3. Bind index wiring (CLI)

`type_annotator.build_bind_index()` / `graphrag_pipeline.get_bind_at_risk()` / `bind_category()` already
existed and `predict()` already threaded them through correctly — the gap was entirely that `cli.py`'s
`build-graph` never produced `bind_index.json`, and both CLI commands hardcoded `bind_index={}` /
`bind_at_risk=[]`, so the mechanism was completely unreachable from the command line. Fixed: `build-graph`
now also writes `bind_index.json` (collected over **all** `.sv/.svh` files regardless of `--scan-all`,
since `*_bind.sv` files and files under `sva/` essentially never match the UVM-keyword filename filter used
for the main class scan); `blast-radius`/`analyze` both gained `--rtl-module`.

Verified on the real OpenTitan `hw/` tree: 64 RTL modules, 88 bind-file references — previously invisible
end-to-end. Sample: `aes -> [aes_cov_bind, aes_bind]`, `adc_ctrl_fsm -> [adc_ctrl_cov_bind, adc_ctrl_bind]`.
Caliptra-rtl (default file set): 16 RTL modules, 16 bind-file references.

**Important caveat, stated plainly in the CLI's own output:** this is informational, not scored.
`bind_category()` returns `"Assertion Orphaning"` for non-coverage bind files, and that category is **not**
in `VALID_CATEGORIES` (the 5-category taxonomy the human-labeled evaluation and `clean_predictions()`
actually score against). A bind-at-risk entry does appear in `analyze`'s `--json` output and in the
`predictions` dict, but it is invisible to every metric `evaluation.py` computes, and to the text-mode
category tables (which only iterate `VALID_CATEGORIES`). Adding a 6th category is a taxonomy decision that
needs new human labels — explicitly out of scope here (see §5, item 3 is the user's own task).

## 4. Evidence citations (item 4)

Every entry in `predict()`'s `predictions` dict now carries an `evidence` block:

```json
"uart_env_cfg": {
  "categories": ["Interface Drift"], "confidence": 0.82, "node_type": "CFG", "source": "llm",
  "evidence": {"graph_path": "inheritance, depth=2", "cited_commits": ["aaaaaaaa"]}
}
```

`graph_path` is `"inheritance, depth=N"` for ordinary blast-radius nodes or `"bind_index"` for bind-at-risk
entries. `cited_commits` is deliberately **not** the full list of Stage-2-retrieved commits — only the ones
whose own `changed_nodes` include *this specific node*, so the citation is a real precedent for that class,
not noise shared across every node in the blast radius. This is a purely additive change (new dict keys
only); `merge_predictions()`'s/`predict()`'s existing `categories`/`confidence`/`node_type`/`source` values
are unchanged, and `run_eval.py`'s evaluation path does not call `predict()` at all — the scored 400-commit
numbers are unaffected by this change by construction, not just by testing.

Association edges (§2) are **not yet** cited inside `evidence` — they are surfaced separately via
`--show-associations` because they are not part of the scored blast radius either. Wiring association-edge
citations into `evidence` would be a natural extension once (and if) association edges are merged into the
scored traversal.

## 4.1 Scored evaluation, 2026-11-08: does the real git-diff signal actually help accuracy?

The gap noted above — "not wired into the 400-commit evaluation" — is closed. `experiments/paper/run_eval.py`
gained a `--change-aware` flag that resolves each unit's real file via `git_diff.render_for_commit()` against
a local checkout and scores a 5th system, `graphrag_change_aware` (VeriDelta + the real `change_summary`),
alongside the existing four. Ran for real, with `ANTHROPIC_API_KEY` set, Haiku-tier, 658 live calls + 302
cache hits over the full 240 scorable units in this run (240, not the 271 estimated from a placeholder-label
dry run, because the real `labels_llm.json` has slightly more units with no labelled descendant than the
dry run's filler labels did — expected, not a bug).

**Result: no statistically significant difference, in either direction.**

| | Filtered ground truth | Unfiltered ground truth |
|---|---|---|
| VeriDelta (baseline) macro-F1 | 0.77 | 0.35 |
| VeriDelta + change_summary macro-F1 | 0.74 | 0.35 |
| Baseline 95% CI (bootstrap) | [0.635, 0.818] | [0.308, 0.379] |
| Change-aware 95% CI (bootstrap) | [0.617, 0.828] | [0.306, 0.380] |
| P(baseline > change-aware) | macro 0.69, micro 0.47 | macro 0.45, micro 0.58 |

The confidence intervals overlap almost completely in both settings, and P(baseline > change-aware) sits near
50% both times — a coin flip, not a real effect either way. The one visible dip (filtered macro-F1 0.77→0.74)
is driven almost entirely by Coverage Regression (only 11 labelled points total in the filtered set — too
small to read as a real effect rather than sampling noise).

**What this means, stated plainly:** the new signal is *correct* (§1 above — validated on real commits, 100%
coverage, real bugs found and fixed) but on this real run it did not translate into measurably better
predictions. Two honest, non-exclusive explanations, neither tested further here:

- Haiku-tier models may already infer "something changed inside this class" well enough from the commit
  message and blast-radius context alone, so a more precise structural diff adds little marginal information
  for *this* task at *this* model tier.
- The categories being predicted (interface drift, sequence invalidation, etc.) may correlate more with
  *which* class changed and *how far downstream* than with the literal text of what changed inside it — i.e.
  the existing graph structure may already be carrying most of the useful signal.

This is reported as a negative result, not hidden or re-run until it looked better: `results/run_change_aware/results.json`
(committed; the per-prompt `llm_cache/` is not — it's gitignored, same as every other run in `results/`,
per the repo's existing convention) carries the full per-unit predicted/actual records for every system, not
just the aggregate table above, so every number here is independently re-checkable from that one file. It
is a single real pass, not cherry-picked from several. A larger sample, a stronger model tier, or
category-specific analysis (RAL Desync and Coverage Regression have the smallest support and the most
erratic numbers across every system here, not just this one) would be needed before concluding the
structural diff *never* helps — only that it didn't, measurably, in this one real run.

## 5. Limitations (what's missing, and what to reconsider)

- **Only two repositories.** OpenTitan and Caliptra-rtl. A third repository (CORE-V-Verif was discussed) is
  not included — the type-classification suffix rules (`type_annotator._RULES`) and the UVM-keyword
  filename filter are both tuned against lowRISC's naming conventions, so a third repo with different
  conventions would likely need its own profile, not just more data. This is also roadmap item 4
  (`docs/roadmap.md`).
- **The 400-commit evaluation sample is a sample**, not the full commit history of either repo, and the
  human-labeled subset is 20 commits (being extended to ~100 — the user's own task, not done by this
  session). Every number reported against that sample should be read as "on this sample," not as a
  repo-wide guarantee.
- **What's deliberately left out of this session's changes**, and why:
  - `change_summary` is not wired into `run_eval.py` (§1) — needs file paths the sample data doesn't store,
    or an API-costing re-run.
  - Association edges (§2) are not merged into the scored blast radius — needs a deliberate before/after
    evaluation, not a silent change to already-published numbers.
  - Assertion Orphaning / Coverage-Regression-from-bind (§3) are not in `VALID_CATEGORIES` — needs new
    human labels (the user's own task, item 3 of the roadmap message this branch implements).
  - Change-notification design (§6) is a design note only, explicitly deferred to a future paper by
    instruction.
- **What to reconsider:** the RAL-generated-boilerplate noise in member_handle edges (§2) is the most
  likely thing worth revisiting first if association edges are ever merged into scoring — right now a
  change to a widely-used RAL base pattern would produce a disproportionately large, low-value blast
  radius in a register-heavy IP.

## 6. Change-notification design (paper-4 groundwork, not implemented)

Sketched per instruction — "keep in mind... for future implementation for paper 4" — not built.

**Shape:** a CI step (roadmap item 7 already named this: "a GitHub Action / CI step that comments the
blast radius and likely failure modes on a pull request"). Concretely:

1. On a PR's `synchronize`/`opened` event, diff the PR against its base to get the list of changed
   `.sv/.svh` files and, per file, the changed class names (reuse `git_diff.find_file_defining_class` in
   reverse, or just parse the diff hunks for `class` headers in the changed region).
2. For each changed class: run `blast-radius` (cheap, no API key) to get the structural radius immediately
   — this alone is useful as a fast, free first comment. Then, if a key is configured, run `analyze` with
   `change_summary` built from the PR's before/after file content (already exactly what `git_diff.py` does,
   just fed `base_sha`/`head_sha` instead of `sha~1`/`sha`).
3. Post one PR comment per changed class (or one comment aggregating all of them, to avoid noise on a PR
   that touches many classes), each citing the `evidence` block from §4 so the comment is checkable, not
   just asserted — "flagged because `X` is 2 inheritance hops downstream, and commit `abc1234` touched the
   same class with a similar change."
4. Open question, explicitly not resolved here: whether the comment should be gated on confidence
   (post only `confidence > threshold` predictions) to avoid alert fatigue, and whether "notify" should mean
   a PR comment, a check-run annotation (inline on the diff), or both. This is a product decision that
   needs real usage data (false-positive rate on real PRs) that this session has no way to produce.

Nothing in this section has been implemented, tested, or validated — it is a plan, written down now so it
does not need to be re-derived later.

## 7. GUI demo polish for sharing (2026-11-08): real evidence, real diff text, full node coverage

Context: the blast-radius explorer (`gui/blast_radius_explorer.html`) was built earlier as a paper-facing
demo over 7 real OpenTitan commits, but it had two known gaps going into this pass: the "What changed
(diff)" panel was a placeholder ("not shown on this page"), and only the ~41% of blast-radius nodes that
also had a benchmark label got a stored model prediction -- the rest showed "not stored, no label", which
reads as "the tool doesn't work for most of the graph" to anyone without the context for why (a USCIS
reviewer, a researcher skimming the page, a startup evaluating the demo). Both are now fixed, and both
fixes used the real pipeline -- nothing on the page is fabricated.

**Part A -- real evidence citations and real diff text** (`gui/add_evidence_and_diffs.py`):
- Evidence citation (`graph_path` + `cited_commits`) for every already-labelled node, computed by exactly
  replaying `graphrag_pipeline.predict()`'s own Item-4 evidence logic (lines 651-666) against the stored
  `similar_commits` retrieval -- no LLM call needed, this is pure post-processing of already-stored
  predictions.
- Real `change_summary` diff text per commit, via `git_diff.render_for_commit()` against the actual local
  OpenTitan checkout (same function `run_eval.py --change-aware` uses, §1).
- Wired into `renderDetail()` (new `renderEvidence()`) and `renderCommit()`'s diff panel.

**Part B -- filled the remaining 95 "not stored" nodes with real predictions**
(`gui/fill_missing_predictions.py`): across the 7 example commits, 95 of 161 blast-radius nodes (59%) had
no stored model prediction, because the *original* evaluation run (`results/run1`) only scored nodes that
also carried a benchmark label -- there was no ground truth to grade the other nodes against, so they were
never asked about. That scoping is correct for computing Table IV's accuracy numbers, but it made the demo
page look broken. Fix: ran the real pipeline (`build_stage1_context` -> `retrieve_similar_commits` ->
`build_prompt` -> `call_llm` -> `merge_predictions`, the same "graphrag" system and methodology already
used for the stored predictions, no `change_summary` mixed in so the two sets are directly comparable) for
every missing node, one real Claude Haiku call per commit (7 calls total, not 95 -- each call covers every
missing node in that commit's blast radius in one prompt). Cost: 7 calls, 7,887 input / 5,228 output
tokens. The user's API key was used only as an ephemeral environment variable for this one run and was
never written to any file (verified with a repo-wide grep after the run).

Also fixed while doing this:
- `verdict()` was computing a false "no overlap" (✗) check for nodes that now have a model prediction but
  no ground truth -- fixed to return "nothing to check" (blank) whenever `n.actual` is absent, not just
  whenever `n.model` is absent.
- The ring graph's filled/hollow dot logic was keyed on `n.model` (which is now true for every node, so
  it would have made the "filled: has a label" legend meaningless) -- switched to `n.actual` so filled
  still means "has a benchmark label," matching what the legend says.
- All "no label" / "not stored" copy renamed to **"not in benchmark"** with an explanatory title/sentence
  ("no commit in the 400-commit benchmark happened to change this class, so there's nothing to grade the
  model's answer against") -- reframes the empty cells from looking like a tool limitation to a stated,
  understood property of the benchmark's label coverage.
- Page copy (bottom note + new "About this project" section) now states the real number that matters:
  **400 real commits scored across two production RTL repositories** (374 OpenTitan + 26 Caliptra-rtl),
  not a number invented for the 7-commit demo page.

**What this does and doesn't mean:** every node in these 7 examples' blast radii now has a real model
prediction and a real type-prior prediction; "not in benchmark" (66/161 nodes still show this, unchanged)
means there is still no ground-truth label to grade that specific prediction against -- that's a property
of the 400-commit benchmark's coverage, not something this pass could or should manufacture. Inventing
ground-truth labels would be fabrication; filling in the model's own predictions is not, because predicting
is what the tool actually does in real use regardless of whether a label exists yet.

Republished to the live artifact: <https://claude.ai/artifact/D67aQHMNx9ewcotHEvRNUJ>.
