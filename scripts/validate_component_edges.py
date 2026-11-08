"""
Corpus-wide validation of component_edges.py against the real OpenTitan and
Caliptra-rtl checkouts, mirroring the change_summary.py validation pass:
run it over everything, report counts per edge kind, timing, and dump a
sample of each kind for manual spot-checking.
"""
import sys
import time

sys.path.insert(0, "/home/claude/veridelta/src")

from veridelta import type_annotator as ta
from veridelta import component_edges as ce

REPOS = [
    ("opentitan", "/home/claude/sources/opentitan/hw"),
    ("caliptra-rtl", "/home/claude/sources/caliptra-rtl"),
]

for name, root in REPOS:
    print(f"\n=== {name} ===")
    t0 = time.time()
    files = ta.collect_files(root, scan_all=True)
    print(f"{len(files)} files")

    # Build the graph the way `veridelta build-graph` does, to get a real
    # known_classes set (node_types.json's keys) instead of a synthetic one.
    all_edges = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                all_edges += ta.extract_class_edges(fh.read())
        except OSError:
            continue
    known_classes = {c for parent, child in all_edges for c in (parent, child)}
    # also add classes with no inheritance edge at all (standalone base classes)
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        t = ta.strip_comments(text)
        for m in ta._CLASS_HEAD.finditer(t):
            known_classes.add(m.group(1))
    print(f"{len(known_classes)} known classes")

    t1 = time.time()
    corpus = ce.build_corpus(files, known_classes)
    t2 = time.time()
    print(f"corpus: {len(corpus)} class bodies extracted in {t2-t1:.2f}s")

    edges = ce.all_component_edges(corpus, known_classes)
    t3 = time.time()
    print(f"edges: {len(edges)} total in {t3-t2:.2f}s")

    by_kind = {}
    for e in edges:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, es in sorted(by_kind.items()):
        print(f"  {kind}: {len(es)}")

    print(f"total wall time: {time.time()-t0:.2f}s")

    print("\n-- sample of each kind --")
    for kind, es in sorted(by_kind.items()):
        print(f"\n[{kind}] ({len(es)} total, showing up to 6)")
        for e in es[:6]:
            print(f"  {e.src} -> {e.dst}  via={e.via!r}  evidence={e.evidence!r}")
