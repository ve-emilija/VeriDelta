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
