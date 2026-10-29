# Contributing

## Setup

```
git clone <this repository> && cd veridelta
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,llm,paper]"
pytest
```

The tests run offline and need no API key.

## Rules that keep the results trustworthy

1. **Never commit an API key.** Keys come from `ANTHROPIC_API_KEY`. `.env` files are ignored by git.
2. **`experiments/paper/` and `results/` are frozen** at the release that accompanies the paper
   (tag `paper-2026`). New work goes into `src/veridelta/` and, for new experiments, `experiments/<name>/`.
3. **Any change that can move a number** (graph extraction, type rules, retrieval, prompt, scoring)
   must say so in `CHANGELOG.md` and either keep `tests/test_scoring.py::test_reported_table_iv_numbers...`
   passing or update it together with a new stored run.
4. **Evaluation commits stay out of retrieval.** Stage 2 may use only commits older than the one analyzed.
   A change that weakens this needs a test that proves the opposite.
5. Report results with both label modes (restricted and unrestricted) and say which one a claim uses.

## Workflow

- Branch from `main`, keep a change small, open a pull request, make sure `pytest` passes.
- Add a test for every bug fix and every new command-line option.
- Bump the version in `src/veridelta/__init__.py` and add a changelog entry when releasing; tag `vX.Y.Z`.
