#!/usr/bin/env bash
# Usage:  ./run.sh /path/to/VeriDelta/src  /path/to/opentitan/hw  /path/to/caliptra-rtl
# Runs slang's parser over every .sv/.svh file of each repo, then compares with VeriDelta's regex extractor.
set -euo pipefail
cd "$(dirname "$0")"
VD_SRC=${1:?path to VeriDelta src/ directory}
OT=${2:?path to opentitan/hw}
CA=${3:?path to caliptra-rtl}
[ -x bin/classdump ] || { echo "run ./build_slang.sh first"; exit 1; }
mkdir -p work results

dump() {  # $1 = name, $2 = root
  # same file universe as VeriDelta's --scan-all: every .sv / .svh below the root
  find "$2" \( -type f -o -type l \) \( -name '*.sv' -o -name '*.svh' \) | LC_ALL=C sort > "work/files_$1.txt"
  tr '\n' '\0' < "work/files_$1.txt" | xargs -0 -n 200 bin/classdump > "work/slang_$1.tsv" 2> "work/slang_$1.err" || true
  echo "$1: $(wc -l < "work/files_$1.txt") files, $(wc -l < "work/slang_$1.tsv") class declarations, $(wc -l < "work/slang_$1.err") unreadable files"
}
dump opentitan "$OT"
dump caliptra  "$CA"

python3 -I compare.py --veridelta "$VD_SRC" \
  --repo opentitan "$OT" work/slang_opentitan.tsv \
  --repo caliptra  "$CA" work/slang_caliptra.tsv \
  --out results | tee results/table.txt
