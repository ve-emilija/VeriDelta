# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/);
while the version is 0.x, minor releases may change the command line and the file formats.

## [Unreleased]

## [0.1.0] - 2026-10-29

First public release. Accompanies the paper *VeriDelta: GraphRAG-Grounded Change Impact Analysis for
UVM Verification Environments*.

### Added
- `veridelta` command line: `build-graph`, `blast-radius`, `analyze` (with `--mock` for offline use).
- Python package `veridelta`: typed class graph (`type_annotator`), three-stage pipeline
  (`graphrag_pipeline`), scoring (`evaluation`).
- Offline test suite (46 tests), including a guard that re-scores the stored predictions and checks
  the numbers of Table IV.
- Paper data: 13,629-commit corpus, OpenTitan and Caliptra graphs, 400 evaluation commits with labels,
  20-commit human check, stored results of two runs (`data/`, `results/`).
- Paper scripts under `experiments/` (evaluation, labeling, sampling, corpus construction).

### Known limits
- Scored evaluation points come from OpenTitan only.
- The history index for a new project has to be built by hand (see `docs/roadmap.md`).
- Assertion Orphaning is not predicted.
