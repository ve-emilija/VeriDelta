"""
Stage 1 GUI polish, part A: compute REAL evidence citations (graph_path +
cited_commits) and REAL change_summary diff text for the 7 example commits
already in gui/blast_radius_explorer.html's DATA array.

No LLM call involved -- evidence citation is pure post-processing (same
exact logic as graphrag_pipeline.predict()'s Item 4 block, lines 651-666),
and change_summary comes straight from git_diff.py against the real local
OpenTitan checkout. Like augment_data.py (the bind-index/association-edge
version of this same idea), this patches the DATA array in
blast_radius_explorer.html in place -- it is not a general tool, it
hardcodes the one file and the one local checkout path this update needed.
"""
import json
import sys

sys.path.insert(0, "/home/claude/veridelta/src")
from veridelta import graphrag_pipeline as gp
from veridelta import git_diff as gd

ROOT = "/home/claude/veridelta"
REPO_DIR = "/home/claude/sources/opentitan"

nt = json.load(open(f"{ROOT}/data/opentitan/node_types.json"))
adj = json.load(open(f"{ROOT}/data/opentitan/adjacency.json"))
commits = json.load(open(f"{ROOT}/data/opentitan/commits.json"))

HTML = f"{ROOT}/gui/blast_radius_explorer.html"
lines = open(HTML, encoding="utf-8").readlines()
PREFIX = "const DATA = "
data_idx = next(i for i, l in enumerate(lines) if l.startswith(PREFIX))
data = json.loads(lines[data_idx][len(PREFIX):].rstrip()[:-1])

out_summary = []
for d in data:
    full_hash = next((h for h in commits if h.startswith(d["hash"]) or d["hash"].startswith(h[:10])), None)
    # d["hash"] in DATA is already the short 10-char form used for display; find the full key.
    full_hash = next((h for h in commits if h.startswith(d["hash"])), None)
    if not full_hash:
        print(f"!! no commits.json entry for {d['hash']} ({d['changed']}) -- skipping evidence")
        continue
    c = commits[full_hash]
    cn = d["changed"]
    ctype = nt.get(cn, {}).get("type", "OTHER")
    pool = {h: v for h, v in commits.items() if h != full_hash}
    sim = gp.retrieve_similar_commits(cn, ctype, c.get("ip_block", d.get("ip")), pool, nt, before=c["date"])

    # Exact Item-4 logic from graphrag_pipeline.predict():
    commits_by_node = {}
    for sc in sim:
        for n in sc.get("changed_nodes", []):
            commits_by_node.setdefault(n, []).append(sc["hash"])

    n_with_evidence = 0
    for node in d["nodes"]:
        if "model" not in node:
            continue  # only predicted (labelled) nodes carry a prediction to cite evidence for
        node["evidence"] = {
            "graph_path": f"inheritance, depth={node['depth']}",
            "cited_commits": [h[:8] for h in commits_by_node.get(node["name"], [])],
        }
        n_with_evidence += 1

    # change_summary: real git-diff text for the changed class at this commit.
    cs = None
    try:
        cs = gd.render_for_commit(REPO_DIR, full_hash, cn)
    except Exception as e:
        cs = None
    d["diff_summary"] = cs

    out_summary.append((d["hash"], cn, n_with_evidence, len(cs) if cs else 0))

for h, cn, nev, csl in out_summary:
    print(f"{h} {cn:30s} evidence-on {nev:2d} nodes | diff_summary {csl} chars")

new_line = PREFIX + json.dumps(data, separators=(",", ": ")) + ";\n"
lines[data_idx] = new_line
with open(HTML, "w", encoding="utf-8") as f:
    f.writelines(lines)
print(f"\npatched {HTML} in place (DATA array line now {len(new_line)} chars)")
