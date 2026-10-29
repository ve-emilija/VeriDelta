# VeriDelta

**Change impact analysis for UVM verification environments.**
Change a shared UVM class and VeriDelta tells you which downstream classes may break, and how:

```
$ veridelta blast-radius cip_base_env_cfg --graph data/opentitan --max-nodes 3
cip_base_env_cfg  type=CFG  ->  3 downstream classes (showing the nearest 3 of 40)
depth  class                   type  structural prior
-----  ----------------------  ----  ----------------
1      ac_range_check_env_cfg  CFG   Interface Drift
1      adc_ctrl_env_cfg        CFG   Interface Drift
1      aes_env_cfg             CFG   Interface Drift
```

It works in three stages:

1. **Graph.** UVM class inheritance (`class X extends Y`) becomes a typed graph. A class gets a type
   (config, agent, scoreboard, sequence, register model, coverage, environment) from its name.
   Breadth-first search from the changed class gives the *blast radius*.
2. **History.** The five most similar older commits (same IP block, same class, same type) are retrieved.
3. **Language model.** Claude Haiku 4.5 predicts, for each class in the blast radius, which of five failure
   categories may manifest: Interface Drift, Sequence Invalidation, Checker Desync, RAL Desync,
   Coverage Regression.

Stage 1 runs in milliseconds and needs no model or key.

## Install

```
pip install -e .                # core (networkx only)
pip install -e ".[llm]"         # official Anthropic client (optional; a stdlib client is used otherwise)
pip install -e ".[dev,paper]"   # tests, and the extra packages for the paper scripts
```

Python 3.10 or newer.

## Use

```
# 1. build the typed graph of a project (for OpenTitan point it at the hw/ folder)
veridelta build-graph path/to/opentitan/hw --out graphs/opentitan

# 2. the downstream classes of a change (no model, no key)
veridelta blast-radius dv_base_env_cfg --graph graphs/opentitan

# 3. predicted failure categories (needs ANTHROPIC_API_KEY, or try it offline with --mock)
export ANTHROPIC_API_KEY=...
veridelta analyze dv_base_env_cfg --graph graphs/opentitan --ip dv --cache-dir .cache
```

`analyze` uses `GRAPH/commits.json` for Stage 2 when it exists (the repository ships it for OpenTitan and
Caliptra under `data/`); without it Stage 2 is skipped. Add `--json` to any command for machine-readable output.

From Python:

```python
from veridelta import get_blast_radius, predict
```

## Results (paper, OpenTitan, 1,673 evaluation points)

| System | Micro F1, type-restricted labels | Micro F1, unrestricted labels |
|---|---|---|
| VeriDelta (graph + history + model) | 0.95 | 0.51 |
| Model + graph, no history | 0.90 | 0.54 |
| Structural prior only (no model) | 0.99 | 0.50 |
| Model without the graph | 0.51 | 0.57 |

Reading the table honestly: the **typed graph is what matters** (removing it drops F1 from 0.95 to 0.51);
history adds a smaller gain and is the only route to RAL Desync. Under type-restricted labels the structural
prior is a ceiling by construction, because those labels follow the same type rule. Unrestricted labels
(2,055 points) remove that effect, and all systems then score about 0.5. Details and limits are in the paper.

## Known limits

- All scored points are from OpenTitan; Caliptra's class hierarchy is too shallow to supply any.
- The graph follows inheritance only and assumes lowRISC-style suffixes (`_env_cfg`, `_scoreboard`, ...).
  On Caliptra 63% of classes fall into the untyped `OTHER` bucket.
- Failure-category labels of the evaluation commits are model-made and human-checked on 20 commits.
- Assertion Orphaning is not predicted: bind files are not classes and have no node in the graph.
- Building the history file for a new project is manual for now (see [docs/roadmap.md](docs/roadmap.md)).

## Repository layout

```
src/veridelta/       the package (graph, pipeline, scoring, command line)
tests/               offline tests, including a guard for the numbers of the paper
experiments/paper/   scripts that produced the paper's evaluation
experiments/corpus/  scripts that built the 13,629-commit corpus
data/                corpus, graphs, evaluation sample, labels, 20-commit human check
results/             stored predictions of the evaluation runs
docs/                pipeline, reproducing the paper, roadmap
```

## Reproduce the paper

See [docs/reproducing-paper.md](docs/reproducing-paper.md). Everything except the live model calls runs
offline: `pytest` re-scores the stored predictions and checks Table IV.

## Cite

Please cite the paper (see `CITATION.cff`).

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
