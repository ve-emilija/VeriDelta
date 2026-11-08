"""
Stage 1 GUI polish, part B: fill in the remaining ~95 "not stored" blast-radius
nodes across the 7 existing example commits with REAL model predictions (same
pipeline, same methodology as the ones already stored -- plain graphrag, no
change_summary, to stay consistent with the existing "model"/"model_src"
fields which were produced the same way in results/run1).

This does NOT add new commits. It completes the Model/Type-prior columns for
nodes that were in the blast radius all along but never asked about, because
the original stored run only scored nodes that also had a benchmark label
(see gui/README.md). Nodes with no ground-truth label still get a real model
prediction here; they just have nothing to grade it against -- that's a
property of the 400-commit benchmark's label coverage, not of the tool, and
the GUI copy was updated separately to say so ("not in benchmark" instead of
"no label").

Needs ANTHROPIC_API_KEY in the environment. Costs one real Haiku call per
commit (7 total), each covering every still-missing node in that commit's
blast radius in one prompt -- not one call per node.
"""
import json
import os
import sys

sys.path.insert(0, "/home/claude/veridelta/src")
from veridelta import graphrag_pipeline as gp
from veridelta import evaluation as ev

ROOT = "/home/claude/veridelta"

nt = json.load(open(f"{ROOT}/data/opentitan/node_types.json"))
adj = json.load(open(f"{ROOT}/data/opentitan/adjacency.json"))
commits = json.load(open(f"{ROOT}/data/opentitan/commits.json"))

HTML = f"{ROOT}/gui/blast_radius_explorer.html"
lines = open(HTML, encoding="utf-8").readlines()
PREFIX = "const DATA = "
data_idx = next(i for i, l in enumerate(lines) if l.startswith(PREFIX))
data = json.loads(lines[data_idx][len(PREFIX):].rstrip()[:-1])

gp.configure_llm(cache_dir=None, temperature=0.0)
client = gp.make_client()

filled_total = 0
for d in data:
    full_hash = next((h for h in commits if h.startswith(d["hash"])), None)
    if not full_hash:
        print(f"!! no commits.json entry for {d['hash']} -- skipping")
        continue
    c = commits[full_hash]
    cn = d["changed"]
    ctype = nt.get(cn, {}).get("type", "OTHER")

    missing = [n for n in d["nodes"] if "model" not in n]
    if not missing:
        print(f"{d['hash']}: nothing missing, skipping")
        continue

    br, meta = gp.get_blast_radius(cn, adj, return_meta=True)
    stage1 = gp.build_stage1_context(cn, br, nt, [])
    pool = {h: v for h, v in commits.items() if h != full_hash}
    sim = gp.retrieve_similar_commits(cn, ctype, c.get("ip_block", d.get("ip")), pool, nt, before=c["date"])
    prompt = gp.build_prompt(stage1, sim, c["ip_block"], nt)
    llm_output = gp.call_llm(prompt, client)
    predictions = gp.merge_predictions(stage1, llm_output, llm_source="llm")

    # Same evidence logic as add_evidence_and_diffs.py / graphrag_pipeline.predict() Item 4.
    commits_by_node = {}
    for sc in sim:
        for n in sc.get("changed_nodes", []):
            commits_by_node.setdefault(n, []).append(sc["hash"])

    prior_preds = ev.predict_structural_prior(br, nt)

    filled = 0
    for node in missing:
        name = node["name"]
        p = predictions.get(name)
        if not p:
            continue  # genuinely nothing to say (shouldn't happen -- every blast-radius node gets a merged prediction)
        node["model"] = p["categories"]
        node["model_src"] = p.get("source", "llm")
        node["evidence"] = {
            "graph_path": f"inheritance, depth={node['depth']}",
            "cited_commits": [h[:8] for h in commits_by_node.get(name, [])],
        }
        pr = prior_preds.get(name)
        if pr:
            node["prior"] = pr["categories"]
        filled += 1
    filled_total += filled
    print(f"{d['hash']} {cn:25s} filled {filled}/{len(missing)} missing nodes "
          f"({gp.LLM_STATS.get('calls', '?')} LLM calls so far, {gp.LLM_STATS.get('total_tokens', '?')} tokens)")

new_line = PREFIX + json.dumps(data, separators=(",", ": ")) + ";\n"
lines[data_idx] = new_line
with open(HTML, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"\ntotal filled: {filled_total}")
print("LLM stats:", dict(gp.LLM_STATS))
