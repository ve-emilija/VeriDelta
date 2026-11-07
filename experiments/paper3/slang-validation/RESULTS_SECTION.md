# Draft: results section for paper 3 (extractor validation)

**Table X. Class-graph extraction: VeriDelta's regex versus the slang parser** (classes / unique inheritance edges; snapshot of each repository at HEAD, November 2026).

| Repository | Regex, paper file filter (A) | Regex, all files (B) | slang parser, all files (C) | Edges only in B | Edges only in C |
|---|---|---|---|---|---|
| OpenTitan (`hw/`) | 1312 / 1287 | 1632 / 1606 | 2128 / 2102 | **0** | 496 |
| Caliptra RTL | 1051 / 1029 | 1183 / 1157 | 1183 / 1157 | **0** | **0** |

**Text.** We validated the regular-expression class extractor against slang, a full SystemVerilog parser with a preprocessor, run at syntax level (no filelists, no UVM library). On both repositories every edge found by the regex was also found by slang (0 regex-only edges), and on Caliptra the two graphs are identical (1157 edges). On OpenTitan slang finds 496 additional edges. All 496 are in the vendored riscv-dv instruction generator and are produced by macro expansion (the child class name never appears as `class <name>` in the source text), so a text-based extractor cannot see them by construction; none belong to the OpenTitan DV environments.

The comparison also shows that the loss in the submitted pipeline comes from the file-name filter, not from the regex: restricting parsing to files whose name contains a UVM keyword hides 19.9% of the inheritance edges in OpenTitan (1287 of 1606) and 11.1% in Caliptra (1029 of 1157) relative to scanning every `.sv`/`.svh` file.

**Limits.** HEAD snapshot, not the commits of the 400-commit evaluation. Syntax-level only, so this validates the class graph, not types resolved through parameters or packages. This is a null result for adding slang to the class-graph stage: it is useful as an independent check, and as a baseline for later edge types (member handles, bind) that a regex cannot reach.
