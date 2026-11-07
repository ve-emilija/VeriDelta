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

With `--uvm-types`, a class that is `OTHER` by name takes the type of the nearest UVM or UVMF library base class
it extends (for example `uvm_reg*` gives RAL, `uvmf_sequence_base` gives SEQ), which does not depend on naming
conventions. The table is `UVM_BASE_TYPES` in `type_annotator.py`.

## Stage 1: blast radius

Breadth-first search from the changed class; depth is the number of inheritance hops. At most 60 classes are kept
(nearest first) to bound the prompt. Each class is annotated with its type and prior.

## Stage 2: history

Each past commit is stored with its date, subject, IP block and changed classes. A commit scores +3 for the same IP
block, +3 if it changed the same class and +2 if one of its classes has the same type. The top five (ties by
recency) go into the prompt with their changed classes and message. Only commits older than the one analyzed are
eligible, and evaluation commits never are. History carries no failure labels.

## Stage 3: model

One prompt per changed class: the changed class, the typed blast radius, the historical examples and the output
specification (JSON, one entry per class). Default model `claude-haiku-4-5-20251001`, temperature 0. Categories
outside the five are dropped; classes the model omits fall back to their prior at confidence 0.40.

## File formats

```
node_types.json   {"uart_env_cfg": {"type": "CFG"}, ...}
adjacency.json    {"dv_base_env_cfg": ["cip_base_env_cfg", ...], ...}
commits.json      {"<hash>": {"date": "2024-05-01T10:00:00", "subject": "...", "ip_block": "uart",
                              "changed_nodes": ["uart_env_cfg"]}, ...}
```
