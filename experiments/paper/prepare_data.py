"""
prepare_data.py  --  build the per-repository inputs of the new evaluation.

For each repository: typed graph at the end of the corpus range (node_types,
adjacency), and a commit index (date in UTC, subject, linked UVM classes,
IP block) for every corpus commit.  Also writes the evaluation sample (400
commits drawn with a fixed seed from the latest 20% of the verification/both
commits that change at least one typed class).
"""
import csv, os, re, sys, json, random, collections
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repository root (data/ and results/ live here)
import networkx as nx
from veridelta import type_annotator as ta

SRC = os.environ.get("VD_SOURCES", os.path.join(ROOT, "sources") + os.sep)
CORPUS = os.environ.get("VD_CORPUS", os.path.join(ROOT, "data", "corpus", "corpus_files.csv"))
COMMITS = os.environ.get("VD_COMMITS", os.path.join(ROOT, "data", "corpus", "commits.csv"))
OUT = os.environ.get("VD_OUT", os.path.join(ROOT, "data")); os.makedirs(OUT, exist_ok=True)
REPOS = ("opentitan", "caliptra-rtl")
N_SAMPLE, SEED, TEST_FRAC = 400, 42, 0.20

def utc(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

def ip_of(repo, path):
    p = path.split("/")
    if repo == "opentitan":
        if p[:2] == ["hw", "ip"] and len(p) > 2: return p[2]
        if len(p) > 3 and p[0] == "hw" and p[1].startswith("top_") and p[2] == "ip_autogen": return p[3]
        if p[:3] == ["hw", "dv", "sv"] or p[:2] == ["hw", "dv"]: return "dv"
        return "/".join(p[:3])
    return p[1] if p[0] == "src" and len(p) > 1 else p[0]

graphs = {}
for repo in REPOS:
    G = nx.DiGraph()
    for f in ta.collect_files(SRC + repo, False):
        for par, ch in ta.extract_class_edges(ta._read(f)): G.add_edge(par, ch)
    ta.annotate_graph(G); graphs[repo] = G
    d = os.path.join(OUT, repo); os.makedirs(d, exist_ok=True)
    json.dump({n: {"type": G.nodes[n]["type"]} for n in G}, open(f"{d}/node_types.json", "w"), indent=0)
    json.dump({n: sorted(G.successors(n)) for n in G}, open(f"{d}/adjacency.json", "w"), indent=0)
    print(repo, "nodes", G.number_of_nodes(), "edges", G.number_of_edges())

subj = {}
for r in csv.DictReader(open(COMMITS, encoding="utf-8", errors="ignore")):
    subj[(r["repo"], r["hash"])] = r["subject"]

C = collections.defaultdict(lambda: {"label": None, "date": None, "cls": {}, "ips": collections.Counter()})
for r in csv.DictReader(open(CORPUS)):
    d = C[(r["repo"], r["hash"])]; d["label"] = r["commit_label"]; d["date"] = utc(r["author_date"])
    base = os.path.basename(r["path"]); stem = re.sub(r"\.svh?$", "", base)
    if base.endswith((".sv", ".svh")) and stem in graphs[r["repo"]]:
        d["cls"][stem] = graphs[r["repo"]].nodes[stem]["type"]; d["ips"][ip_of(r["repo"], r["path"])] += 1

index = {r: {} for r in REPOS}
for (repo, h), d in C.items():
    if d["cls"]:
        index[repo][h] = {"date": d["date"], "label": d["label"], "subject": subj.get((repo, h), ""),
                          "changed_nodes": sorted(d["cls"]), "ip_block": d["ips"].most_common(1)[0][0]}
for repo in REPOS:
    json.dump(index[repo], open(f"{OUT}/{repo}/commits.json", "w"))
    print(repo, "commits linked to a graph class:", len(index[repo]))

# The 400 evaluation commits are PINNED to the draw already made (seed 42, latest 20% of the
# verification/both commits with a typed class); the 20 hand-checked commits are its first 20.
PIN = os.environ.get("VD_PIN", os.path.join(ROOT, "data", "sample400_pin.json"))
pinned = [(o["repo"], o["hash"]) for o in json.load(open(PIN))]
sample = []
for k in pinned:
    d = C[k]
    sample.append({"repo": k[0], "hash": k[1], "date": d["date"], "label": d["label"],
                   "classes": sorted(c for c, t in d["cls"].items() if t != "OTHER"),
                   "types": {c: t for c, t in d["cls"].items() if t != "OTHER"},
                   "ip_block": d["ips"].most_common(1)[0][0], "subject": subj.get(k, "")})
json.dump(sample, open(f"{OUT}/sample400.json", "w"), indent=1)
print("sample", len(sample), "| earliest", min(x["date"] for x in sample)[:10], "| both:",
      sum(x["label"] == "both" for x in sample), "| repos:", dict(collections.Counter(x["repo"] for x in sample)))
