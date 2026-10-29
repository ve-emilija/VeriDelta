# Roadmap

Planned, not yet done. Ordered by expected value.

1. **History builder for any repository.** `veridelta build-history`: read `git log`, link changed `.sv/.svh`
   files to graph classes, derive an IP block from a configurable path rule. Today the history file exists only for
   OpenTitan and Caliptra (built by `experiments/paper/prepare_data.py`).
2. **Facts from the diff in the prompt.** RAL Desync is limited because the model cannot see which field of a
   configuration class changed; adding only the commit message did not help. Try diff-derived flags.
3. **"Prior plus evidence" mode.** Start from the structural prior and let the model only add categories it can
   support with evidence from history or the diff.
4. **More projects** (CORE-V, other RISC-V cores) and a naming-convention profile per project, so the type function
   is not tied to lowRISC suffixes.
5. **Ground truth from real regression failures** instead of model-labeled commits.
6. **Assertion Orphaning:** match bind files to the RTL modules they reference and give them graph nodes.
7. **Integration:** a GitHub Action / CI step that comments the blast radius and likely failure modes on a pull
   request (about 9 s per change); JSON/SARIF output.
8. **Project hygiene:** lint and type checks in CI, a PyPI release, a larger human-checked sample.
