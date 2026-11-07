#!/usr/bin/env python3
"""
Compare VeriDelta's regex class-inheritance extractor with the slang SystemVerilog parser.

Standard library only (no pip, no networkx). It imports `extract_class_edges` and
`collect_files` from the VeriDelta repo, so the regex side is exactly the code in the paper.

    python3 compare.py --veridelta ../veridelta/src \
        --repo opentitan  /path/to/opentitan/hw        work/slang_opentitan.tsv \
        --repo caliptra   /path/to/caliptra-rtl        work/slang_caliptra.tsv \
        --out results

For each repo it reports the graph size under four extractors:
  A  regex, paper file filter (file NAME must contain a UVM keyword)    <- what the paper used
  B  regex, every .sv/.svh file (--scan-all)
  C  slang, every .sv/.svh file (real parser, preprocessor applied)
and the edge-level agreement between B and C (the fair like-for-like comparison).
"""
import argparse
import json
import os
import re
import sys


def load_regex_edges(files, extract, read):
    edges = {}
    for f in files:
        for parent, child in extract(read(f)):
            edges.setdefault((parent, child), f)
    return edges


def load_slang_edges(tsv):
    edges = {}
    with open(tsv, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[2]:
                continue                       # class without an `extends` clause
            f, child, parent = parts[0], parts[1], parts[2]
            edges.setdefault((parent, child), f)
    return edges


def nodes_of(edges):
    n = set()
    for p, c in edges:
        n.add(p)
        n.add(c)
    return n


def declared_literally(child, path, cache):
    """True if the file text contains `class <child>` verbatim. False means the class
    only exists after macro expansion (e.g. `class `NAME`), which a text regex cannot see."""
    if path not in cache:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            cache[path] = fh.read()
    return re.search(r"\bclass\s+(?:automatic\s+|static\s+|virtual\s+|interface\s+)*" + re.escape(child) + r"\b",
                     cache[path]) is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--veridelta", required=True, help="path to the VeriDelta repo's src/ directory")
    ap.add_argument("--repo", nargs=3, action="append", metavar=("NAME", "ROOT", "SLANG_TSV"), required=True)
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    sys.path.insert(0, a.veridelta)
    from veridelta import type_annotator as ta       # networkx is imported lazily; not needed here

    os.makedirs(a.out, exist_ok=True)
    summary = {}
    for name, root, tsv in a.repo:
        paper_files = ta.collect_files(root, scan_all=False)
        all_files = ta.collect_files(root, scan_all=True)
        A = load_regex_edges(paper_files, ta.extract_class_edges, ta._read)
        B = load_regex_edges(all_files, ta.extract_class_edges, ta._read)
        C = load_slang_edges(tsv)
        # restrict slang to the same file universe as the regex scan-all
        universe = {os.path.realpath(f) for f in all_files}
        C = {e: f for e, f in C.items() if os.path.realpath(f) in universe}

        regex_only = sorted(set(B) - set(C))
        slang_only = sorted(set(C) - set(B))
        cache = {}
        macro = [e for e in slang_only if not declared_literally(e[1], C[e], cache)]
        literal = [e for e in slang_only if e not in set(macro)]

        with open(os.path.join(a.out, f"slang_only_{name}.tsv"), "w") as fh:
            fh.write("parent\tchild\tfile\tclass_text_in_file\n")
            for e in slang_only:
                fh.write(f"{e[0]}\t{e[1]}\t{C[e]}\t{'yes' if e not in set(macro) else 'no (macro-generated)'}\n")
        with open(os.path.join(a.out, f"regex_only_{name}.tsv"), "w") as fh:
            fh.write("parent\tchild\tfile\n")
            for e in regex_only:
                fh.write(f"{e[0]}\t{e[1]}\t{B[e]}\n")

        summary[name] = {
            "files_paper_filter": len(paper_files), "files_all": len(all_files),
            "A_regex_paper_filter": {"nodes": len(nodes_of(A)), "edges": len(A)},
            "B_regex_scan_all": {"nodes": len(nodes_of(B)), "edges": len(B)},
            "C_slang": {"nodes": len(nodes_of(C)), "edges": len(C)},
            "regex_only_edges_B_minus_C": len(regex_only),
            "slang_only_edges_C_minus_B": len(slang_only),
            "slang_only_macro_generated": len(macro),
            "slang_only_literal_in_text": len(literal),
            "edges_invisible_to_paper_filter_pct_of_B": round(100 * (1 - len(A) / len(B)), 1) if B else None,
            "nodes_invisible_to_paper_filter_pct_of_B": round(100 * (1 - len(nodes_of(A)) / len(nodes_of(B))), 1) if B else None,
        }
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    # ---- printable table ----
    def cell(d): return f"{d['nodes']} / {d['edges']}"
    rows = [("repo", "files (paper / all)", "A regex, paper filter", "B regex, all files", "C slang",
             "B-only edges", "C-only edges", "of which macro-generated")]
    for n, s in summary.items():
        rows.append((n, f"{s['files_paper_filter']} / {s['files_all']}", cell(s["A_regex_paper_filter"]),
                     cell(s["B_regex_scan_all"]), cell(s["C_slang"]), str(s["regex_only_edges_B_minus_C"]),
                     str(s["slang_only_edges_C_minus_B"]), str(s["slang_only_macro_generated"])))
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    print("graph size = classes / inheritance edges\n")
    for i, r in enumerate(rows):
        print("  ".join(c.ljust(w[j]) for j, c in enumerate(r)))
        if i == 0:
            print("  ".join("-" * x for x in w))
    print(f"\nwritten: {a.out}/summary.json, slang_only_*.tsv, regex_only_*.tsv")


if __name__ == "__main__":
    main()
