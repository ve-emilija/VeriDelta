#!/usr/bin/env bash
# Builds ./bin/classdump: a ~40-line program on top of the slang SystemVerilog parser.
# Needs: git, cmake >= 3.28, a C++20 compiler (Xcode command line tools on macOS). No pip.
set -euo pipefail
cd "$(dirname "$0")"
SLANG_COMMIT=6fbb158f156436a1cb4d0ce9de5373a416a05d4c      # pinned: the version used for the reported numbers
if [ ! -d work/slang-src/.git ]; then
  mkdir -p work
  git clone https://github.com/MikePopoloski/slang.git work/slang-src
fi
git -C work/slang-src checkout --quiet "$SLANG_COMMIT"
cmake -S classdump -B work/build -DSLANG_DIR="$PWD/work/slang-src" -DCMAKE_BUILD_TYPE=Release
cmake --build work/build --target classdump -j "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
mkdir -p bin
cp work/build/classdump bin/classdump
echo "built bin/classdump (slang $SLANG_COMMIT)"
