# VeriDelta blast-radius explorer

An interactive, single-file HTML page over the stored paper-evaluation results: pick one of the 7 real
OpenTitan commits used as qualitative examples, see the inheritance blast radius as rings, compare the
model's answer against the structural type-prior and the (model-generated, not human-checked) labels, and
see the real bind-index and association-edge data for the same classes from the same OpenTitan checkout.
Also shown per commit: the real `change_summary` diff text, and per prediction, the real evidence citation
(graph path + which past commits it's grounded in) straight from `graphrag_pipeline.predict()`'s own
evidence step. Every node in every one of the 7 blast radii carries a real model prediction (filled
2026-11-08 — see `docs/change_summary_validation.md` §7); nodes with no ground-truth label are marked
"not in benchmark" rather than left blank, since that's a property of the 400-commit benchmark's label
coverage, not a gap in the tool.

Published and kept up to date at <https://claude.ai/artifact/D67aQHMNx9ewcotHEvRNUJ> (private; share it from
its own page if it needs to go in the paper or be shown to someone else). `blast_radius_explorer.html` in
this directory is a snapshot of that published page, kept here so the page's content and the data it was
built from are reproducible from the repo alone, not only from the live artifact.

**What's real vs. illustrative:** every number in the page — the blast radius, the model/prior/label
columns, the bind-index entries, the association edges, the diff text, the evidence citations — is from a
real run (the stored paper evaluation in `results/run1`, a real `veridelta build-graph` pass, a real
`git diff` against the OpenTitan checkout, or the pipeline's own evidence-citation step). Nothing is
fabricated for the demo. See `docs/change_summary_validation.md` for how the bind index and association
edges were validated (and §4.1 for the real, negative result of testing whether the diff signal actually
helps accuracy), and the note at the bottom of the page itself for what each panel does and does not mean.

`augment_data.py` computed the bind-index/association-edge additions for the 7 commits already in the
page's `DATA` array (it loads `bind_index.json`/`association_edges.json` from a `build-graph` run and
merges `bind_at_risk`/`associations` fields into each commit's entry). `add_evidence_and_diffs.py` computed
the evidence-citation and diff-text additions the same way (no LLM call needed — evidence citation is pure
post-processing of the already-stored predictions, replaying the exact logic in
`graphrag_pipeline.predict()`; the diff text comes straight from `git_diff.py` against the same checkout).
`fill_missing_predictions.py` filled in the model/type-prior predictions for the nodes that still had none
— 95 of 161, because the original evaluation run only scored nodes with a benchmark label — with 7 real,
non-cached Claude Haiku calls (one per commit, each covering every missing node in that commit's blast
radius in a single prompt; not 95 separate calls). All three are kept here so the exact computations are
re-runnable, not just described; none is a general tool — each hardcodes the path to a specific local
checkout/graph directory and in-place-edits the one line of the HTML file that holds `DATA`, because that
is what each one update needed.

## How to open it

No build step, no server, no dependencies — it's one self-contained `.html` file (styling and data are
inlined). Any of these works:

```bash
git pull                                    # once you have the repo cloned
open gui/blast_radius_explorer.html         # macOS
# or on Linux/Windows: double-click the file, or drag it into a browser tab
```

Or just open the live, always-current copy: <https://claude.ai/artifact/D67aQHMNx9ewcotHEvRNUJ> (private to
the owner's account; share it from its own page if someone outside the account needs the link).

If you'd rather serve it than open it as a local file (e.g. to get a `localhost` link instead of a `file://`
one), `python3 -m http.server` from inside `gui/` and visit `http://localhost:8000/blast_radius_explorer.html`.

## Does this update itself when someone commits something new?

**No — this is a static snapshot, not a live tool.** The 7 commits in the page's `DATA` array are fixed,
hand-picked paper examples; nothing about this page watches the repo, a branch, or new commits, and opening
it after a new commit lands changes nothing in it. Concretely, two different things stay still when work
continues:

- **New commits to *this* VeriDelta repo** (another feature, another bugfix) have no effect on the page at
  all — it has no connection to this repo's own git history, only to the *subject-matter* repos
  (OpenTitan/Caliptra-rtl) it demonstrates the tool against.
- **A new commit in OpenTitan/Caliptra-rtl you'd like to add as an 8th example** needs a manual step: run
  the pipeline (`build-graph`, then `blast-radius`/`analyze`) on that commit to get real data, shape it into
  one more entry in the `DATA` array (same fields as the existing 7 — see `augment_data.py` for the
  bind-index/association half of that shape), and republish. Nothing does this automatically today.

A real "tell me automatically when a new commit needs attention" system is sketched — not built — in
`docs/change_summary_validation.md` §6 (a CI step that comments a PR's blast radius automatically). This
page is a paper-facing demo of 7 fixed examples, not that system.
