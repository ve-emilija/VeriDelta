# Reproducing the paper

Install with `pip install -e ".[dev,paper]"`. All paths are relative to the repository root.

## Without an API key

```
pytest                                          # re-scores the stored predictions; checks Table IV
python experiments/paper/make_table4.py         # redraws Table IV into figures/
python experiments/paper/run_eval.py --dry-run  # counts units, calls and tokens
python experiments/paper/run_eval.py --mock     # whole pipeline with a fake model
```

## Rebuild the data (needs clones under `sources/`)

Clone [lowRISC/opentitan](https://github.com/lowRISC/opentitan) and
[chipsalliance/caliptra-rtl](https://github.com/chipsalliance/caliptra-rtl) into `sources/`. The versions used are in
`data/corpus/manifest.json` (collected 24 October 2026).

```
python experiments/paper/prepare_data.py        # graphs, commit index, evaluation sample (identical to data/)
veridelta build-graph sources/opentitan --out /tmp/g   # same graph, through the command line
```

## Live runs (about 5 USD in total)

```
export ANTHROPIC_API_KEY=...
python experiments/paper/label_commits.py                      # labels of the 400 commits (Claude Sonnet)
python experiments/paper/run_eval.py --out-dir results/run1
python experiments/paper/run_eval.py --out-dir results/run2
python experiments/paper/run_eval.py --msg --out-dir results/run_msg
```

## Corpus construction (Section III)

`experiments/corpus/`: `stage1_extract_commits.py` (all commits and changed files), `stage2a_extension_inventory.py`,
`stage2_filter_commits.py` (HDL commits, rule-based design/verification/both labels) and `stage2c_review.py`
(label check against a human annotator and an AI model). The processed result is `data/corpus/corpus_files.csv`.

## Protocol in one paragraph

Of the 5,864 verification/"both" commits that change at least one typed class, the latest 20% (from 2024-08-25)
form the test pool; 400 are drawn with seed 42 (374 OpenTitan, 26 Caliptra) and labeled by Claude Sonnet. An
evaluation point is a class in the blast radius that an evaluation commit changed (1,673 restricted, 2,055
unrestricted). Stage 2 never sees an evaluation commit or the future.
