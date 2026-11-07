# Extractor validation: VeriDelta regex vs. the slang parser

One question: **does VeriDelta's regex class-inheritance extractor build the same graph as a real SystemVerilog parser (slang)?**

No pip, no Python packages. You need `git`, `cmake` (3.28 or newer), and a C++20 compiler.

## Run it (macOS)

```bash
# 0. one-time tools (skip what you already have)
xcode-select --install          # compiler
brew install cmake              # needs Homebrew; or download cmake from cmake.org

# 1. get the sources (shallow clones are enough)
mkdir -p ~/veridelta-validation && cd ~/veridelta-validation
git clone --depth 1 https://github.com/lowRISC/opentitan.git
git clone --depth 1 https://github.com/chipsalliance/caliptra-rtl.git
git clone https://github.com/ve-emilija/VeriDelta.git

# 2. unzip this folder here, then build the parser program (10-30 min, one time)
cd slang-validation
./build_slang.sh

# 3. run (about a minute)
./run.sh ../VeriDelta/src ../opentitan/hw ../caliptra-rtl
```

Output: `results/table.txt` (the table), `results/summary.json`, and two edge lists:
`results/slang_only_*.tsv` (edges only slang finds) and `results/regex_only_*.tsv` (edges only the regex finds).

## What is being compared

| column | what it is |
|---|---|
| A | VeriDelta regex with the paper's file filter (a file is parsed only if its **name** contains a UVM keyword) |
| B | same regex on **every** `.sv`/`.svh` file (`--scan-all`) |
| C | slang's parser on every `.sv`/`.svh` file, preprocessor applied, **no** filelists, **no** UVM library needed |

Graph size is counted the way VeriDelta counts it: classes that appear in at least one `extends` edge, and unique (parent, child) edges.
B versus C is the like-for-like comparison of extractors. A versus B is the effect of the file-name filter.

## Caveats (say them in the paper)

* Snapshot of the repositories at the time you clone (HEAD), not the exact commits of the 400-commit evaluation.
* Syntax-level parsing only. slang is not asked to elaborate the design, so this checks the **class graph**, not types resolved through parameters or packages.
* "Macro-generated" means: the child class name never appears as `class <name>` in the file text (it is created by a macro such as `` `DEFINE_INSTR``), so a text regex cannot see it by construction.
* The build script has been tested on Linux only. I have not run it on macOS.
