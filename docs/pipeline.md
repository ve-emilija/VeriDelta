# How VeriDelta works

## Graph and types

`veridelta build-graph` scans the SystemVerilog files whose names contain a UVM keyword
(`agent`, `env`, `vseq`, `reg`, `cov`, ... or every `.sv/.svh` file with `--scan-all`) and reads
`class X extends Y` declarations into a directed graph (edge parent -> child). Parameter blocks between the
class name and `extends` are skipped, including nested parentheses; comments and strings are ignored.

The type function maps a class name to a type; the first matching rule wins, so `uart_env_cfg` is `CFG`, not `ENV`.

| Type | Examples of suffix | Primary (prior) category |
|---|---|---|
| CFG, ENV, AGENT | `_env_cfg`, `_env`, `_agent` | Interface Drift |
| SEQ | `_vseq`, `_seq` | Sequence Invalidation |
| SCBD | `_scoreboard` | Checker Desync |
| RAL | `_reg_block` | RAL Desync |
| COV | `_cov` | Coverage Regression |
| OTHER | everything else | none |
| SVA | `_sva`, `_bind` | none (reserved for assertion bind files; no class in the OpenTitan or Caliptra graphs has this type) |

## Stage 1: blast radius

Breadth-first search from the changed class; depth is the number of inheritance hops. At most 60 classes are kept
(nearest first) to bound the prompt. Each class is annotated with its type and prior.

## Stage 2: history

Each past commit is stored with its date, subject, IP block and changed classes. A commit scores +3 for the same IP
block, +3 if it changed the same class and +2 if one of its classes has the same type. The top five (ties by
recency) go into the prompt with their changed classes and message. Only commits older than the one analyzed are
eligible, and evaluation commits never are. History carries no failure labels.

## Stage 3: model

One prompt per changed class: the changed class, the typed blast radius, optionally a change-aware
structural summary of what changed inside the class itself (`change_summary.py`/`git_diff.py` — members,
methods, constraints, macros and curated API-call deltas; not yet wired into the 400-commit evaluation,
see `docs/change_summary_validation.md`), the historical examples and the output specification (JSON, one
entry per class). Default model `claude-haiku-4-5-20251001`, temperature 0. Categories outside the five are
dropped; classes the model omits fall back to their prior at confidence 0.40.

Every prediction returned by `predict()` (CLI `analyze`) carries an `evidence` block citing the graph path
it was reached by (`"inheritance, depth=N"` or `"bind_index"`) and the specific past commits (by hash) that
changed that same node and were used in Stage 2 — see `docs/change_summary_validation.md` §4.

## Beyond inheritance: bind index and association edges

`build-graph` also writes two more files, both **informational** — neither is merged into the `adjacency`
BFS that produces the scored blast radius above:

- `bind_index.json`: RTL module name → `bind` files referencing it (stale bind statements are "Assertion
  Orphaning"/"Coverage Regression"). `blast-radius --rtl-module X` / `analyze --rtl-module X` surface this.
  Note: "Assertion Orphaning" is not in `VALID_CATEGORIES`, so this is not scored by `evaluation.py`.
- `association_edges.json`: member handles, `uvm_config_db` set/get, analysis-port `.connect()`, and
  factory overrides — real UVM coupling that class inheritance alone cannot see. `blast-radius
  --show-associations` surfaces one-hop edges from the changed class with their literal source evidence.

See `docs/change_summary_validation.md` for how each was validated against the real OpenTitan/Caliptra-rtl
checkouts, every bug found and fixed, and what each still misses.

## File formats

```
node_types.json         {"uart_env_cfg": {"type": "CFG"}, ...}
adjacency.json          {"dv_base_env_cfg": ["cip_base_env_cfg", ...], ...}
commits.json            {"<hash>": {"date": "2024-05-01T10:00:00", "subject": "...", "ip_block": "uart",
                                    "changed_nodes": ["uart_env_cfg"]}, ...}
bind_index.json         {"aes": ["aes_cov_bind", "aes_bind"], ...}
association_edges.json  {"tl_agent": [{"dst": "cip_base_env", "kind": "member_handle",
                                       "via": "m_tl_agents", "evidence": "tl_agent[string] m_tl_agents;"}, ...], ...}
```
