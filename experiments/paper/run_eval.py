"""
run_eval.py -- Section V evaluation on the two-repository corpus.

For every evaluation commit (400, time split) and every changed typed class with a non-empty blast
radius that contains a labelled node, run the four systems: VeriDelta, LLM without history,
structural prior, LLM without graph.  Stage 2 retrieves only commits OLDER than the evaluation commit
and never an evaluation commit.  Ground truth: a node is labelled with the failure categories of the
evaluation commits that changed it (held out: the retrieval pool carries no categories at all).

  python experiments/paper/run_eval.py --dry-run            count calls and tokens, no API
  python experiments/paper/run_eval.py --mock               offline plumbing test with a fake model
  python experiments/paper/run_eval.py --labels data/labels_llm.json [--limit N]
"""
import argparse, json, os, re, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repository root (data/ and results/ live here)
from veridelta import graphrag_pipeline as gp
from veridelta import evaluation as ev
from veridelta import git_diff as gd
from veridelta.ground_truth_mapper import TYPE_ALLOWED_CATEGORIES
from veridelta.mock import MockClient as Mock

REPOS = ("opentitan", "caliptra-rtl")

# Local checkout for each repo, used only when --change-aware asks for a real
# git-diff-based change_summary (git_diff.py needs a real working tree, not
# just the pre-extracted commits.json this script otherwise relies on).
# Override with VERIDELTA_<REPO>_DIR if your checkouts live somewhere else.
REPO_DIRS = {
    "opentitan":    os.environ.get("VERIDELTA_OPENTITAN_DIR", "/home/claude/sources/opentitan"),
    "caliptra-rtl": os.environ.get("VERIDELTA_CALIPTRA_DIR", "/home/claude/sources/caliptra-rtl"),
}


def build_change_summary(repo: str, commit_hash: str, class_name: str) -> str | None:
    """
    change_aware Stage 3 text for `class_name` as changed BY `commit_hash`, or
    None if it can't be built (checkout missing locally, class not found, or
    the commit/its parent isn't fetched -- all of which degrade to "no
    change_summary for this unit" rather than raising, same as git_diff.py's
    own policy elsewhere).
    """
    repo_dir = REPO_DIRS.get(repo)
    if not repo_dir or not os.path.isdir(repo_dir):
        return None
    try:
        return gd.render_for_commit(repo_dir, commit_hash, class_name)
    except Exception:
        return None
def load_repo(repo):
    d = os.path.join(ROOT, "data", repo)
    return {"node_types": json.load(open(f"{d}/node_types.json")), "adjacency": json.load(open(f"{d}/adjacency.json")),
            "commits": json.load(open(f"{d}/commits.json"))}

def build_gt(sample, labels, node_types, filtered):
    gt = collections.defaultdict(set)
    for c in sample:
        cats = set(labels.get(c["hash"], {}).get("categories", []))
        for n in c["classes"]:
            t = node_types.get(n, {}).get("type", "OTHER")
            gt[n] |= (cats & set(TYPE_ALLOWED_CATEGORIES.get(t, []))) if filtered else cats
    return {n: sorted(v) for n, v in gt.items() if v}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=os.path.join(ROOT, "data", "labels_llm.json"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "results", "run")); ap.add_argument("--cache-dir")
    ap.add_argument("--temperature", type=float, default=0.0); ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--mock", action="store_true")
    ap.add_argument("--bootstrap", type=int, default=1000); ap.add_argument("--msg", action="store_true", help="add the VeriDelta+message variant")
    ap.add_argument("--change-aware", action="store_true",
                     help="add a graphrag_change_aware variant that feeds a real git-diff change_summary "
                          "(members/methods/constraints/calls, incl. method-body-only changes) into Stage 3, "
                          "on top of plain graphrag -- needs the real OpenTitan/Caliptra-rtl checkouts "
                          "locally (REPO_DIRS above) with each unit's commit and its parent fetched")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    sample_all = json.load(open(os.path.join(ROOT, "data", "sample400.json")))
    if a.dry_run or a.mock and not os.path.exists(a.labels):
        labels = {c["hash"]: {"categories": ["Sequence Invalidation"]} for c in sample_all}   # placeholder, counts only
    else:
        labels = json.load(open(a.labels))
    gp.configure_llm(a.cache_dir or os.path.join(a.out_dir, "llm_cache" + ("_mock" if a.mock else "")), a.temperature)
    client = Mock() if a.mock else (None if a.dry_run else gp.make_client())
    if not a.mock and not a.dry_run: print("model:", gp.MODEL, "| temperature:", a.temperature)
    eval_hashes = {c["hash"] for c in sample_all}
    body = {}
    if a.msg:
        import csv
        for row in csv.DictReader(open(os.path.join(ROOT, "data", "corpus", "commits.csv"), encoding="utf-8", errors="ignore")):
            lines = [l for l in (row["subject"] + "\n" + row["body"]).splitlines()
                     if l.strip() and not l.lower().startswith(("signed-off-by", "co-authored-by"))]
            body[row["hash"]] = " ".join(lines)[:700]
        ev.SYSTEMS = ev.SYSTEMS + ["graphrag_msg"]; ev.SYSTEM_LABELS["graphrag_msg"] = "VeriDelta + message"
    if a.change_aware:
        ev.SYSTEMS = ev.SYSTEMS + ["graphrag_change_aware"]
        ev.SYSTEM_LABELS["graphrag_change_aware"] = "VeriDelta + change_summary"

    recs = {"filtered": {s: [] for s in ev.SYSTEMS}, "unfiltered": {s: [] for s in ev.SYSTEMS}}
    stats = collections.Counter(); chars = 0
    units = []                                   # (repo, commit, changed class, blast radius, ...) in time order
    ctx = {}
    for repo in REPOS:
        R = load_repo(repo); nt, adj = R["node_types"], R["adjacency"]
        rs = [c for c in sample_all if c["repo"] == repo]
        sample = rs[: a.limit] if a.limit else rs
        gt_f, gt_u = build_gt(rs, labels, nt, True), build_gt(rs, labels, nt, False)
        pool = {h: v for h, v in R["commits"].items() if h not in eval_hashes}
        ctx[repo] = (nt, gt_f, gt_u, pool)
        print(f"{repo}: {len(sample)} eval commits | pool {len(pool)} older+other commits | labelled nodes {len(gt_f)} (filtered) / {len(gt_u)} (unfiltered)")
        for c in sorted(sample, key=lambda x: x["date"]):
            stats["commits"] += 1; hit_commit = False
            for cn in c["classes"]:
                br, meta = gp.get_blast_radius(cn, adj, return_meta=True)
                if not br: stats["no_descendants"] += 1; continue
                if not [1 for n, _ in br if n in gt_f or n in gt_u]: stats["no_labelled_descendant"] += 1; continue
                stats["calls_units"] += 1; stats["truncated"] += meta["truncated"]; hit_commit = True
                units.append((repo, c, cn, br))
            stats["commits_with_points"] += hit_commit

    def process(u):
        repo, c, cn, br = u
        nt, gt_f, gt_u, pool = ctx[repo]
        stage1 = gp.build_stage1_context(cn, br, nt, [])
        ctype = nt.get(cn, {}).get("type", "OTHER")
        sim = gp.retrieve_similar_commits(cn, ctype, c["ip_block"], pool, nt, before=c["date"])
        prompt = gp.build_prompt(stage1, sim, c["ip_block"], nt)
        if a.dry_run:
            return (len(sim) == 0, len(prompt) + len(gp.build_prompt(stage1, [], c["ip_block"], nt)), None, False)
        raw = {"graphrag": gp.merge_predictions(stage1, gp.call_llm(prompt, client), llm_source="llm"),
               "llm_no_history": ev.predict_llm_no_history(cn, c["ip_block"], br, nt, [], client),
               "structural_prior": ev.predict_structural_prior(br, nt),
               "llm_no_graph": ev.predict_llm_no_graph(cn, c["ip_block"], br, nt, client)}
        if a.msg:
            pm = gp.build_prompt(stage1, sim, c["ip_block"], nt, change_message=body.get(c["hash"], c["subject"]))
            raw["graphrag_msg"] = gp.merge_predictions(stage1, gp.call_llm(pm, client), llm_source="llm")
        change_summary_found = None
        if a.change_aware:
            change_summary_found = build_change_summary(repo, c["hash"], cn)
            pca = gp.build_prompt(stage1, sim, c["ip_block"], nt, change_summary=change_summary_found)
            raw["graphrag_change_aware"] = gp.merge_predictions(stage1, gp.call_llm(pca, client), llm_source="llm")
        out = {"filtered": [], "unfiltered": []}
        for mode, gt in (("filtered", gt_f), ("unfiltered", gt_u)):
            for n, _d in br:
                if n not in gt: continue
                for s_, rr in raw.items():
                    p = rr.get(n)
                    out[mode].append((s_, {"commit": f"{repo[:3]}:{c['hash'][:8]}", "changed_node": cn, "node": n,
                                           "node_type": nt.get(n, {}).get("type", "OTHER"),
                                           "predicted": p["categories"] if p else [], "actual": gt[n],
                                           "ip_block": c["ip_block"], "source": p["source"] if p else None}))
        return (len(sim) == 0, 0, out, change_summary_found is not None)

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    with ThreadPoolExecutor(1 if (a.dry_run or a.mock) else 8) as ex:
        results = list(ex.map(process, units))
    for empty, ch, out, cs_found in results:
        stats["pool_empty"] += empty; chars += ch
        if a.change_aware:
            stats["change_summary_built" if cs_found else "change_summary_missing"] += 1
        if out:
            for mode in out:
                for s_, r in out[mode]: recs[mode][s_].append(r)
    print(f"  {len(units)} units processed in {time.time()-t0:.0f}s")
    print(dict(stats))
    if a.dry_run:
        n_systems = 3 + (1 if a.msg else 0) + (1 if a.change_aware else 0)  # LLM-calling systems only
        print(f"approx prompt tokens for VeriDelta + no-history: {chars/4:,.0f} (input); "
              f"{stats['calls_units']} units -> ~{n_systems*stats['calls_units']} LLM calls"
              + (" (incl. +1/unit for --change-aware)" if a.change_aware else ""))
        return
    out = {}
    for mode in ("filtered", "unfiltered"):
        agg = {s: ev.dedupe_records(r) for s, r in recs[mode].items()}
        met = {s: ev.compute_metrics(r) for s, r in agg.items()}
        extra = {"by_node_type": {s: ev.metrics_by_node_type(r) for s, r in agg.items()},
                 "bootstrap": ev.bootstrap_ci(agg, n_boot=a.bootstrap),
                 "fallback": {s: ev.fallback_rates(r) for s, r in recs[mode].items()}}
        out[mode] = {"metrics": met, "extra": extra, "n_points": {s: len(r) for s, r in agg.items()}}
        print("\n" + "=" * 70 + f"\nGROUND TRUTH: {mode}\n" + "=" * 70)
        print(ev.format_report(met, extra, title=f"Table IV ({mode} ground truth)"))
    json.dump({"stats": dict(stats), "llm_stats": dict(gp.LLM_STATS), "results": out, "records": recs},
              open(os.path.join(a.out_dir, "mock_results.json" if a.mock else "results.json"), "w"))
    print("\nLLM stats:", dict(gp.LLM_STATS))

if __name__ == "__main__":
    main()
