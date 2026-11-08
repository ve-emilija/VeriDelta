# VeriDelta blast-radius explorer

An interactive, single-file HTML page over the stored paper-evaluation results: pick one of the 7 real
OpenTitan commits used as qualitative examples, see the inheritance blast radius as rings, compare the
model's answer against the structural type-prior and the (model-generated, not human-checked) labels, and
— added 2026-11-08 — see the real bind-index and association-edge data for the same classes from the same
OpenTitan checkout.

Published and kept up to date at <https://claude.ai/artifact/D67aQHMNx9ewcotHEvRNUJ> (private; share it from
its own page if it needs to go in the paper or be shown to someone else). `blast_radius_explorer.html` in
this directory is a snapshot of that published page, kept here so the page's content and the data it was
built from are reproducible from the repo alone, not only from the live artifact.

**What's real vs. illustrative:** every number in the page — the blast radius, the model/prior/label
columns, the bind-index entries, the association edges — is from a real run (either the stored paper
evaluation in `results/run1`, or a real `veridelta build-graph` pass on the OpenTitan checkout for the two
additions). Nothing is fabricated for the demo. See `docs/change_summary_validation.md` for how the bind
index and association edges were validated, and the note at the bottom of the page itself for what each
panel does and does not mean.

`augment_data.py` is the script that computed the bind-index/association-edge additions for the 7 commits
already in the page's `DATA` array (it loads `bind_index.json`/`association_edges.json` from a
`build-graph` run and merges `bind_at_risk`/`associations` fields into each commit's entry) — kept here so
the exact computation is re-runnable, not just described. It is not a general tool: it hardcodes the path
to a specific graph directory and in-place-edits one specific line of the HTML file, because that is what
this one update needed; a future extension (e.g. adding `evidence` citations per node, or regenerating the
whole `DATA` array from a fresh pipeline run rather than patching it) would need its own script, not an
extension of this one.

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
