"""
VeriDelta — Evaluation
evaluation.py

Evaluates VeriDelta against three baselines on the held-out test commits.

SYSTEMS EVALUATED
─────────────────────────────────────────────────────────────────────
1. VeriDelta (GraphRAG)     Stage 1 + Stage 2 + Stage 3 (full pipeline)
2. LLM without history      Stage 1 + Stage 3, no few-shot retrieval
3. Structural prior only    Stage 1 only, τ(node)→category, no LLM
4. LLM without graph        LLM sees only changed node name + IP block

EVALUATION PROTOCOL
─────────────────────────────────────────────────────────────────────
Split: 80/20 stratified by primary category (categories[0]), seed 42.
  Train (retrieval pool): 325 commits      Test (evaluation): 78 commits
  (403 graph-resolvable commits; the old docstring said 323/80.)

For each test commit, for each changed node, Stage 1 gives the blast radius.
Every blast-radius node that has a ground-truth label is an evaluation point;
predicted vs. actual categories are compared per (node, category).

GROUND-TRUTH MODES  (--gt-mode)
─────────────────────────────────────────────────────────────────────
aggregate  The labels in ground_truth.json: for each node, the union of the
           categories of EVERY commit (train and test) that touched it, then
           type-filtered.  This is the protocol of the submitted paper.  It
           leaks: a node's label can come from a train commit that is also
           retrieved as a few-shot example, so Stage 2 can "predict" a label
           it was shown.  `leakage_audit()` quantifies this.
heldout    (default) The label of a node is the type-filtered union of the
           categories of the TEST-split commits that touched it.  Labels never
           come from the retrieval pool, so Stage 2 cannot copy them.

What is unchanged and must be stated in the paper: for every type except CFG
and OTHER the type filter makes the label a function of τ(node), so the
structural-prior baseline is correct for those nodes by construction.  The
`by_node_type` table shows exactly where the baselines can differ.

METRICS
─────────────────────────────────────────────────────────────────────
Records are de-duplicated to unique (commit, node) — the same node reached
from several changed nodes of one commit is ONE evaluation point; predictions
are unioned (--no-dedupe reproduces the old per-(commit, changed_node, node)
counting).  Reported: per-category P/R/F1, macro over categories with support,
micro P/R/F1, false positives in zero-support categories (which the macro
cannot see), by-node-type micro metrics, fallback rates, and a
commit-cluster bootstrap CI (evaluation points from one commit are not
independent: one commit supplies ~25% of the points and all RAL Desync support).

OUTPUT   (in --out-dir, default ./results — never overwrites the inputs)
─────────────────────────────────────────────────────────────────────
  evaluation_results.json   — config, metrics, records, stats
  table3.txt                — paper-ready table + diagnostics

OFFLINE
─────────────────────────────────────────────────────────────────────
  python evaluation.py --reanalyze evaluation_results.json
re-scores previously generated predictions (no API calls, no key) under
--gt-mode / dedupe, and prints the leakage audit and bootstrap CIs.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

from . import graphrag_pipeline as gp
from .graphrag_pipeline import (
    MODEL,
    VALID_CATEGORIES,
    TYPE_PRIMARY_CATEGORY,
    load_data,
    default_dirs,
    get_blast_radius,
    build_stage1_context,
    retrieve_similar_commits,
    build_prompt,
    call_llm,
    merge_predictions,
    llm_text,
    parse_llm_json,
)
from .ground_truth_mapper import TYPE_ALLOWED_CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────

TEST_RATIO  = 0.20
RANDOM_SEED = 42

SYSTEMS = ["graphrag", "llm_no_history", "structural_prior", "llm_no_graph"]
SYSTEM_LABELS = {
    "graphrag":         "VeriDelta (GraphRAG)",
    "llm_no_history":   "LLM — no commit history",
    "structural_prior": "Structural prior only",
    "llm_no_graph":     "LLM — no graph context",
}
CAT_SHORT = {
    "Interface Drift":       "IF",
    "Sequence Invalidation": "SI",
    "Checker Desync":        "CD",
    "RAL Desync":            "RD",
    "Coverage Regression":   "CR",
    "Assertion Orphaning":   "AO",
}
TABLE_CATS = ["Interface Drift", "Sequence Invalidation", "Checker Desync",
              "RAL Desync", "Coverage Regression"]


# ── Train / test split ────────────────────────────────────────────────────────

def split_commits(
    commit_index: dict,
    test_ratio:   float = TEST_RATIO,
    seed:         int   = RANDOM_SEED,
) -> tuple[set[str], set[str]]:
    """
    80/20 stratified split by primary category (categories[0]).

    Only commits with at least one changed_node are included.  Returns
    (train_hashes, test_hashes).  (Logic is deliberately unchanged so the
    325/78 split of the submitted paper is reproduced exactly.)
    """
    eligible = {h: d for h, d in commit_index.items() if d.get("changed_nodes")}

    by_category: dict[str, list[str]] = defaultdict(list)
    for h, d in eligible.items():
        cats = d.get("categories", [])
        primary = cats[0] if cats else "uncategorized"
        by_category[primary].append(h)

    rng = random.Random(seed)
    test_hashes: set[str] = set()
    for cat, hashes in by_category.items():
        rng.shuffle(hashes)
        n_test = max(1, int(len(hashes) * test_ratio))
        test_hashes.update(hashes[:n_test])

    train_hashes = set(eligible.keys()) - test_hashes
    return train_hashes, test_hashes


# ── Ground truth modes ────────────────────────────────────────────────────────

def build_actual_map(
    gt_mode:      str,
    ground_truth: dict,
    commit_index: dict,
    node_types:   dict,
    test_hashes:  set[str],
) -> dict[str, list[str]]:
    """node → actual categories under the chosen protocol (empty ⇒ not evaluated)."""
    if gt_mode == "aggregate":
        return {n: list(d["categories"]) for n, d in ground_truth.items() if d["categories"]}
    if gt_mode != "heldout":
        raise ValueError(f"unknown gt_mode {gt_mode!r}")

    out: dict[str, list[str]] = {}
    for node, d in ground_truth.items():
        cats: set[str] = set()
        for h in d.get("commit_hashes", []):
            if h in test_hashes and h in commit_index:
                cats.update(commit_index[h].get("categories", []))
        allowed = TYPE_ALLOWED_CATEGORIES.get(node_types.get(node, {}).get("type", "OTHER"), [])
        kept = sorted(c for c in cats if c in allowed)
        if kept:
            out[node] = kept
    return out


# ── Baselines ─────────────────────────────────────────────────────────────────

def predict_structural_prior(
    blast_radius: list[tuple[str, int]],
    node_types:   dict,
) -> dict:
    """
    Baseline — structural prior only: τ(node) → primary category, no LLM.
    NB: for non-CFG, non-OTHER nodes this is exactly the rule that built the
    type-filtered ground truth, so it scores ≈1.0 there by construction.
    """
    predictions: dict = {}
    for node, depth in blast_radius:
        ntype   = node_types.get(node, {}).get("type", "OTHER")
        primary = TYPE_PRIMARY_CATEGORY.get(ntype)
        if primary:
            predictions[node] = {
                "categories": [primary],
                "confidence": 0.70,
                "node_type":  ntype,
                "source":     "structural_prior",
            }
    return predictions


def no_graph_prompt(changed_node: str, ip_block: str) -> str:
    """
    The LLM-without-graph prompt: node NAME and IP block only.  (The submitted
    prompt also stated the component type τ, which is graph-derived
    information and contradicted the paper's description of this baseline.)
    """
    return (
        f"You are a UVM verification expert.\n"
        f"A DV engineer changed component: {changed_node}\n"
        f"  IP block: {ip_block}\n\n"
        f"Which DV failure categories will be at risk in downstream components?\n"
        f"Valid categories (use exact strings only):\n"
        f"  {', '.join(VALID_CATEGORIES)}\n\n"
        f"Output ONLY valid JSON — no markdown, no explanation:\n"
        f'{{"categories": ["Category1"], "confidence": 0.85}}'
    )


def predict_llm_no_graph(
    changed_node:  str,
    ip_block:      str,
    blast_radius:  list[tuple[str, int]],
    node_types:    dict,
    client,
) -> dict:
    """
    Baseline — LLM without graph context.  The LLM sees the changed node's name
    and IP block; its categories are applied uniformly to every blast-radius
    node (the radius itself still comes from Stage 1, otherwise there would be
    no nodes to score).

    If the reply cannot be parsed the baseline ABSTAINS (no prediction) and the
    event is counted; it no longer falls back to the graph-derived structural
    prior.  API errors propagate instead of being swallowed.
    """
    text, _ = llm_text(no_graph_prompt(changed_node, ip_block), client, max_tokens=256)
    parsed, _ok = parse_llm_json(text)
    cats = parsed.get("categories", []) if isinstance(parsed, dict) else []
    if isinstance(cats, str):
        cats = [cats]
    cats = [c for c in cats if c in VALID_CATEGORIES] if isinstance(cats, list) else []
    try:
        conf = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError, AttributeError):
        conf = 0.5

    if not cats:
        gp.LLM_STATS["no_graph_abstain"] += 1
        return {}

    return {
        node: {
            "categories": cats,
            "confidence": max(0.30, conf - depth * 0.04),
            "node_type":  node_types.get(node, {}).get("type", "OTHER"),
            "source":     "llm_no_graph",
        }
        for node, depth in blast_radius
    }


def predict_llm_no_history(
    changed_node: str,
    ip_block:     str,
    blast_radius: list[tuple[str, int]],
    node_types:   dict,
    bind_at_risk: list[str],
    client,
) -> dict:
    """Baseline — full graph context in the prompt, but no few-shot history."""
    stage1 = build_stage1_context(changed_node, blast_radius, node_types, bind_at_risk)
    prompt = build_prompt(stage1, [], ip_block, node_types)
    preds  = merge_predictions(stage1, call_llm(prompt, client), llm_source="llm_no_history")
    for p in preds.values():
        if p["source"] == "structural_prior":
            p["source"] = "structural_prior_fallback"
    return preds


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics(eval_records: list[dict]) -> dict:
    """
    Per-category and macro/micro Precision / Recall / F1.

    eval_records: list of {predicted: [cats], actual: [cats]}
    Multi-label: every (node, category) is an independent TP/FP/FN.

    macro   = mean over categories with support > 0 (the submitted definition).
    micro   = pooled TP/FP/FN over all categories (counts FPs everywhere).
    fp_outside_support = FPs in categories that have no ground truth at all —
              invisible to `macro`, so reported separately.
    """
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()

    for rec in eval_records:
        pred   = set(rec["predicted"])
        actual = set(rec["actual"])
        for cat in VALID_CATEGORIES:
            in_pred, in_actual = cat in pred, cat in actual
            if in_pred and in_actual:
                tp[cat] += 1
            elif in_pred:
                fp[cat] += 1
            elif in_actual:
                fn[cat] += 1

    def prf(t, f_p, f_n):
        p = t / (t + f_p) if (t + f_p) else 0.0
        r = t / (t + f_n) if (t + f_n) else 0.0
        f = 2 * p * r / (p + r) if (p + r) else 0.0
        return p, r, f

    metrics: dict = {}
    for cat in VALID_CATEGORIES:
        p, r, f1 = prf(tp[cat], fp[cat], fn[cat])
        metrics[cat] = {
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "support": tp[cat] + fn[cat],
            "tp": tp[cat], "fp": fp[cat], "fn": fn[cat],
        }

    active = [m for m in metrics.values() if m["support"] > 0]
    n = len(active)
    metrics["macro"] = {
        "precision": round(sum(m["precision"] for m in active) / n, 4) if n else 0.0,
        "recall":    round(sum(m["recall"]    for m in active) / n, 4) if n else 0.0,
        "f1":        round(sum(m["f1"]        for m in active) / n, 4) if n else 0.0,
        "n_categories": n,
    }
    T, F_P, F_N = sum(tp.values()), sum(fp.values()), sum(fn.values())
    mp, mr, mf = prf(T, F_P, F_N)
    metrics["micro"] = {"precision": round(mp, 4), "recall": round(mr, 4), "f1": round(mf, 4),
                        "tp": T, "fp": F_P, "fn": F_N}
    metrics["fp_outside_support"] = {
        CAT_SHORT[c]: fp[c] for c in VALID_CATEGORIES
        if metrics[c]["support"] == 0 and fp[c] > 0
    }
    return metrics


def dedupe_records(records: list[dict]) -> list[dict]:
    """
    Collapse to unique (commit, node).  Predictions are the union over the
    commit's changed nodes that reach the node; `actual` is node-level so it is
    identical across duplicates.
    """
    groups: dict[tuple, dict] = {}
    for r in records:
        key = (r["commit"], r["node"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {**r, "predicted": set(r["predicted"]),
                               "changed_nodes": [r["changed_node"]],
                               "sources": [r.get("source")]}
        else:
            g["predicted"].update(r["predicted"])
            g["changed_nodes"].append(r["changed_node"])
            g["sources"].append(r.get("source"))
    out = []
    for g in groups.values():
        g["predicted"] = [c for c in VALID_CATEGORIES if c in g["predicted"]]
        out.append(g)
    return out


def metrics_by_node_type(records: list[dict]) -> dict:
    by_t: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_t[r.get("node_type", "OTHER")].append(r)
    res = {}
    for t, rs in sorted(by_t.items()):
        m = compute_metrics(rs)
        res[t] = {"n": len(rs), **m["micro"]}
    return res


def bootstrap_ci(
    records_by_system: dict[str, list[dict]],
    n_boot: int = 1000,
    seed:   int = 0,
) -> dict:
    """
    Commit-cluster bootstrap (resample COMMITS with replacement) on the same
    resamples for every system.  Returns per-system 95% intervals for
    macro-F1 and micro-F1, and P(VeriDelta > baseline) for each baseline.
    """
    commits = sorted({r["commit"] for rs in records_by_system.values() for r in rs})
    if not commits:
        return {}
    idx = {s: defaultdict(list) for s in records_by_system}
    for s, rs in records_by_system.items():
        for r in rs:
            idx[s][r["commit"]].append(r)

    rng = random.Random(seed)
    macro: dict[str, list[float]] = {s: [] for s in records_by_system}
    micro: dict[str, list[float]] = {s: [] for s in records_by_system}
    for _ in range(n_boot):
        draw = [commits[rng.randrange(len(commits))] for _ in commits]
        for s in records_by_system:
            sample = [r for c in draw for r in idx[s].get(c, ())]
            m = compute_metrics(sample)
            macro[s].append(m["macro"]["f1"])
            micro[s].append(m["micro"]["f1"])

    def ci(v):
        v = sorted(v)
        return [round(v[int(0.025 * len(v))], 4), round(v[int(0.975 * len(v)) - 1], 4)]

    out = {"n_boot": n_boot, "n_commits": len(commits), "systems": {}, "p_graphrag_better": {}}
    for s in records_by_system:
        out["systems"][s] = {"macro_f1_ci95": ci(macro[s]), "micro_f1_ci95": ci(micro[s])}
    if "graphrag" in records_by_system:
        for s in records_by_system:
            if s == "graphrag":
                continue
            out["p_graphrag_better"][s] = {
                "macro": round(sum(a > b for a, b in zip(macro["graphrag"], macro[s])) / n_boot, 3),
                "micro": round(sum(a > b for a, b in zip(micro["graphrag"], micro[s])) / n_boot, 3),
            }
    return out


def leakage_audit(
    records:      list[dict],
    ground_truth: dict,
    commit_index: dict,
    train_hashes: set[str],
    test_hashes:  set[str],
) -> dict:
    """
    For every true positive (node, category) in `records`, where did the label
    come from?  A TP whose label is sourced ONLY from train commits is
    derivable from the retrieval pool (few-shot examples list those commits'
    categories and changed nodes); a TP sourced from a test commit is not.
    """
    total = test_sourced = train_only = 0
    per_cat: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        gt = ground_truth.get(r["node"])
        if not gt:
            continue
        for cat in set(r["predicted"]) & set(r["actual"]):
            srcs = [h for h in gt.get("commit_hashes", [])
                    if cat in commit_index.get(h, {}).get("categories", [])]
            total += 1
            per_cat[cat]["tp"] += 1
            if any(h in test_hashes for h in srcs):
                test_sourced += 1
                per_cat[cat]["test_sourced"] += 1
            elif srcs and all(h in train_hashes for h in srcs):
                train_only += 1
                per_cat[cat]["train_only"] += 1
    return {"tp": total, "test_sourced": test_sourced, "train_only": train_only,
            "per_category": {CAT_SHORT[c]: dict(v) for c, v in per_cat.items()}}


def fallback_rates(records: list[dict]) -> dict:
    """Share of predictions that came from the structural prior instead of the LLM."""
    if not any("source" in r and r["source"] for r in records):
        return {"n_predictions": 0, "from_structural_fallback": 0, "fallback_rate": None,
                "sources": {}, "note": "records carry no source field (generated by the old code)"}
    c = Counter(r.get("source") for r in records if r.get("predicted"))
    n = sum(c.values())
    fb = sum(v for k, v in c.items() if k and "structural_prior" in k)
    return {"n_predictions": n, "from_structural_fallback": fb,
            "fallback_rate": round(fb / n, 4) if n else None, "sources": dict(c)}


# ── Evaluation loop ───────────────────────────────────────────────────────────

def run_evaluation(
    test_hashes:   set[str],
    train_hashes:  set[str],
    data:          dict,
    actual_map:    dict[str, list[str]],
    client,
    max_commits:   int | None = None,
) -> tuple[dict, dict]:
    """
    Run all four systems on every test commit.
    Returns ({system: [raw records per (commit, changed_node, node)]}, stats).
    """
    adjacency    = data["adjacency"]
    node_types   = data["node_types"]
    bind_index   = data["bind_index"]       # noqa: F841  (RTL module unknown at commit level)
    commit_index = data["commit_index"]
    gt_nodes     = set(actual_map)

    train_commit_index = {h: d for h, d in commit_index.items() if h in train_hashes}

    records: dict[str, list] = {s: [] for s in SYSTEMS}
    stats = Counter()
    cap_seen_full: set = set()
    cap_seen_capped: set = set()

    test_list = sorted(test_hashes)
    if max_commits:
        test_list = test_list[:max_commits]

    print(f"\n  Evaluating {len(test_list)} test commits across 4 systems...")
    print(f"  {'Commit':<12} {'Nodes':>6} {'GT hits':>8} {'Status'}")
    print("  " + "─" * 50)

    for chash in test_list:
        commit = commit_index[chash]
        changed_nodes = commit.get("changed_nodes", [])
        ip_block      = commit.get("ip_block", "unknown")
        commit_gt_hits = 0

        for changed_node in changed_nodes:
            blast_radius, meta = get_blast_radius(changed_node, adjacency, return_meta=True)
            if not blast_radius:
                stats["skipped_no_blast_radius"] += 1
                continue

            # Coverage bookkeeping: GT nodes the cap hides are never scored.
            full_radius = get_blast_radius(changed_node, adjacency, max_nodes=10**9)
            for n, _ in full_radius:
                if n in gt_nodes:
                    cap_seen_full.add((chash, n))
            for n, _ in blast_radius:
                if n in gt_nodes:
                    cap_seen_capped.add((chash, n))
            if meta["truncated"]:
                stats["truncated_radii"] += 1

            bind_at_risk = []    # RTL module not known from commit_index

            gt_in_blast = [(n, d) for n, d in blast_radius if n in gt_nodes]
            if not gt_in_blast:
                continue

            commit_gt_hits += len(gt_in_blast)
            actual_by_node = {n: actual_map[n] for n, _ in gt_in_blast}

            stage1 = build_stage1_context(changed_node, blast_radius, node_types, bind_at_risk)

            # System 1: VeriDelta
            similar = retrieve_similar_commits(
                changed_node, node_types.get(changed_node, {}).get("type", "OTHER"),
                ip_block, train_commit_index, node_types
            )
            prompt = build_prompt(stage1, similar, ip_block, node_types)
            graphrag_raw = merge_predictions(stage1, call_llm(prompt, client), llm_source="llm")

            # System 2: LLM without history
            nohist_raw = predict_llm_no_history(
                changed_node, ip_block, blast_radius, node_types, bind_at_risk, client)

            # System 3: structural prior
            prior_raw = predict_structural_prior(blast_radius, node_types)

            # System 4: LLM without graph
            nograph_raw = predict_llm_no_graph(
                changed_node, ip_block, blast_radius, node_types, client)

            raw_by_system = {
                "graphrag": graphrag_raw, "llm_no_history": nohist_raw,
                "structural_prior": prior_raw, "llm_no_graph": nograph_raw,
            }
            for node, actual in actual_by_node.items():
                for sys_name, raw in raw_by_system.items():
                    p = raw.get(node)
                    records[sys_name].append({
                        "commit":       chash[:10],
                        "changed_node": changed_node,
                        "node":         node,
                        "node_type":    node_types.get(node, {}).get("type", "OTHER"),
                        "predicted":    p["categories"] if p else [],
                        "actual":       actual,
                        "ip_block":     ip_block,
                        "source":       p["source"] if p else None,
                    })

        status = "ok" if commit_gt_hits > 0 else "no GT"
        print(f"  {chash[:10]}  {len(changed_nodes):>6}  {commit_gt_hits:>8}  {status}")

    stats["gt_points_in_full_radius"]   = len(cap_seen_full)
    stats["gt_points_in_capped_radius"] = len(cap_seen_capped)
    return records, dict(stats)


# ── Output formatting ─────────────────────────────────────────────────────────

def format_report(
    all_metrics: dict[str, dict],
    extra:       dict | None = None,
    title:       str = "Table III: VeriDelta category-level F1 vs baselines",
) -> str:
    """Paper table + diagnostics.  Categories with no ground truth print 'n/a'."""
    extra = extra or {}
    support = {}
    for m in all_metrics.values():
        for c in TABLE_CATS:
            support[c] = max(support.get(c, 0), m[c]["support"])
    cats = TABLE_CATS

    header = (f"{'System':<26} " + " ".join(f"{CAT_SHORT[c]:>5}" for c in cats)
              + f" {'Macro':>6} {'Micro':>6} {'FP@n/a':>7}")
    sep = "─" * len(header)
    L = [title,
         "(IF=Interface Drift, SI=Sequence Invalidation, CD=Checker Desync, RD=RAL Desync,",
         " CR=Coverage Regression.  Macro = mean over categories",
         " with ground truth; n/a = no ground truth; FP@n/a = false positives in those",
         " categories, which Macro cannot see.)",
         "", "Support (unique evaluation points per category): "
         + "  ".join(f"{CAT_SHORT[c]}={support[c]}" for c in cats), "",
         sep, header, sep]

    for s in SYSTEMS:
        if s not in all_metrics:
            continue
        m = all_metrics[s]
        row = f"{SYSTEM_LABELS[s]:<26} "
        for c in cats:
            row += f"{m[c]['f1']:>5.2f} " if m[c]["support"] > 0 else f"{'n/a':>5} "
        row += f"{m['macro']['f1']:>6.2f} {m['micro']['f1']:>6.2f} "
        row += f"{sum(m['fp_outside_support'].values()):>7d}"
        L.append(row)
    L += [sep, ""]

    for s in SYSTEMS:
        if s not in all_metrics:
            continue
        m = all_metrics[s]
        L.append(f"\n{SYSTEM_LABELS[s]} — Precision / Recall / F1 / Support (TP FP FN)")
        L.append("─" * 70)
        for c in cats:
            cm = m[c]
            if cm["support"] == 0 and cm["fp"] == 0:
                continue
            tag = "" if cm["support"] else "   [no ground truth]"
            L.append(f"  {c:<24} P={cm['precision']:.2f}  R={cm['recall']:.2f}  "
                     f"F1={cm['f1']:.2f}  n={cm['support']:<4} ({cm['tp']} {cm['fp']} {cm['fn']}){tag}")
        mac, mic = m["macro"], m["micro"]
        L.append(f"  {'MACRO (' + str(mac['n_categories']) + ' cats)':<24} P={mac['precision']:.2f}  R={mac['recall']:.2f}  F1={mac['f1']:.2f}")
        L.append(f"  {'MICRO':<24} P={mic['precision']:.2f}  R={mic['recall']:.2f}  F1={mic['f1']:.2f}")

    if "by_node_type" in extra:
        L += ["", "", "Micro-F1 by node type (n = unique evaluation points)", "─" * 70]
        types = sorted({t for s in extra["by_node_type"].values() for t in s})
        L.append(f"  {'type':<7} {'n':>5}  " + "  ".join(f"{s[:9]:>9}" for s in SYSTEMS))
        for t in types:
            n = max(extra["by_node_type"].get(s, {}).get(t, {}).get("n", 0) for s in SYSTEMS)
            vals = []
            for s in SYSTEMS:
                v = extra["by_node_type"].get(s, {}).get(t)
                vals.append(f"{v['f1']:>9.2f}" if v else f"{'-':>9}")
            L.append(f"  {t:<7} {n:>5}  " + "  ".join(vals))
        L.append("  (types other than CFG/OTHER have labels fixed by τ — the structural prior is")
        L.append("   right on them by construction; only CFG/OTHER rows discriminate.)")

    if "bootstrap" in extra and extra["bootstrap"]:
        b = extra["bootstrap"]
        L += ["", "", f"Commit-cluster bootstrap ({b['n_boot']} resamples of {b['n_commits']} commits), 95% CI",
              "─" * 70]
        for s in SYSTEMS:
            if s in b["systems"]:
                v = b["systems"][s]
                L.append(f"  {SYSTEM_LABELS[s]:<26} macro-F1 {v['macro_f1_ci95']}   micro-F1 {v['micro_f1_ci95']}")
        for s, v in b["p_graphrag_better"].items():
            L.append(f"  P(VeriDelta > {SYSTEM_LABELS[s]}):  macro {v['macro']:.2f}   micro {v['micro']:.2f}")

    if "leakage" in extra:
        k = extra["leakage"]
        L += ["", "", "Leakage audit (VeriDelta true positives)", "─" * 70,
              f"  TPs: {k['tp']}   label sourced from a TEST commit: {k['test_sourced']}   "
              f"sourced ONLY from train/retrieval-pool commits: {k['train_only']}"]
        for c, v in k["per_category"].items():
            L.append(f"    {c}: {v}")

    if "fallback" in extra:
        L += ["", "", "Predictions that came from the structural prior instead of the LLM", "─" * 70]
        for s, v in extra["fallback"].items():
            if v["n_predictions"]:
                L.append(f"  {SYSTEM_LABELS[s]:<26} {v['from_structural_fallback']}/{v['n_predictions']}  "
                         f"({100 * (v['fallback_rate'] or 0):.1f}%)")
            else:
                L.append(f"  {SYSTEM_LABELS[s]:<26} not recorded ({v.get('note', 'no predictions')})")

    if "notes" in extra:
        L += ["", ""] + list(extra["notes"])
    return "\n".join(L)


def print_results(all_metrics: dict[str, dict], extra: dict | None = None) -> str:
    out = format_report(all_metrics, extra)
    print(out)
    return out


# ── Scoring pipeline shared by the live run and --reanalyze ──────────────────

def score(
    raw_records:  dict[str, list[dict]],
    *,
    dedupe:       bool,
    data:         dict | None,
    ground_truth: dict | None,
    train_hashes: set[str] | None,
    test_hashes:  set[str] | None,
    n_boot:       int,
) -> tuple[dict, dict, dict]:
    recs = {s: (dedupe_records(r) if dedupe else r) for s, r in raw_records.items() if r}
    metrics = {s: compute_metrics(r) for s, r in recs.items()}
    extra: dict = {
        "by_node_type": {s: metrics_by_node_type(r) for s, r in recs.items()},
        "fallback":     {s: fallback_rates(r) for s, r in recs.items() if s in ("graphrag", "llm_no_history")},
    }
    if n_boot:
        extra["bootstrap"] = bootstrap_ci(recs, n_boot)
    if data and ground_truth and train_hashes is not None and "graphrag" in recs:
        extra["leakage"] = leakage_audit(recs["graphrag"], ground_truth, data["commit_index"],
                                         train_hashes, test_hashes)
    return metrics, extra, recs


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    d_scripts, d_data = default_dirs()
    ap = argparse.ArgumentParser(description="VeriDelta four-system evaluation")
    ap.add_argument("--scripts-dir", default=d_scripts)
    ap.add_argument("--data-dir",    default=d_data)
    ap.add_argument("--out-dir",     default="results")
    ap.add_argument("--gt-mode",     choices=["heldout", "aggregate"], default="heldout")
    ap.add_argument("--no-dedupe",   action="store_true")
    ap.add_argument("--max-commits", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="sampling temperature for Stage 3 and the LLM baselines (default 0)")
    ap.add_argument("--cache-dir",   default=None,
                    help="on-disk LLM response cache (default <out-dir>/llm_cache)")
    ap.add_argument("--bootstrap",   type=int, default=1000)
    ap.add_argument("--reanalyze",   metavar="RESULTS_JSON", default=None,
                    help="re-score existing predictions offline (no API calls)")
    args = ap.parse_args(argv)

    print("=" * 60)
    print("VeriDelta  —  evaluation.py")
    print("=" * 60)

    print("\nLoading data files...")
    data = load_data(args.scripts_dir, args.data_dir)
    gt_path = os.path.join(args.data_dir, "ground_truth.json")
    if not os.path.exists(gt_path):
        print(f"Missing: {gt_path}")
        return 1
    with open(gt_path, encoding="utf-8") as f:
        ground_truth = json.load(f)
    print(f"  Graph nodes:  {len(data['node_types'])}   Commits: {len(data['commit_index'])}   "
          f"GT nodes: {len(ground_truth)}")

    train_hashes, test_hashes = split_commits(data["commit_index"])
    print(f"  Split: train {len(train_hashes)} (retrieval pool) / test {len(test_hashes)}")
    actual_map = build_actual_map(args.gt_mode, ground_truth, data["commit_index"],
                                  data["node_types"], test_hashes)
    print(f"  GT mode: {args.gt_mode}  ({len(actual_map)} labelled nodes)")

    os.makedirs(args.out_dir, exist_ok=True)
    config = {
        "test_ratio": TEST_RATIO, "seed": RANDOM_SEED,
        "n_test": len(test_hashes), "n_train": len(train_hashes),
        "gt_mode": args.gt_mode, "dedupe": not args.no_dedupe,
        "max_commits": args.max_commits, "model": MODEL,
        "temperature": args.temperature, "max_blast_nodes": gp.MAX_BLAST_NODES,
    }

    if args.reanalyze:
        with open(args.reanalyze, encoding="utf-8") as f:
            old = json.load(f)
        raw = old["records"]
        # Re-label under the chosen protocol; predictions are untouched.
        if args.gt_mode != "aggregate":
            for s in raw:
                raw[s] = [{**r, "actual": actual_map[r["node"]]} for r in raw[s] if r["node"] in actual_map]
        stats = {"reanalyzed_from": os.path.basename(args.reanalyze),
                 "original_config": old.get("config", {})}
        config["reanalysis"] = True
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("\nError: ANTHROPIC_API_KEY not set (use --reanalyze for offline scoring).")
            return 1
        cache = args.cache_dir or os.path.join(args.out_dir, "llm_cache")
        gp.configure_llm(cache, args.temperature)
        client = gp.make_client()
        n_eval = min(len(test_hashes), args.max_commits or len(test_hashes))
        print(f"\n  LLM calls: up to 3 per (commit, changed node) with a labelled descendant; "
              f"{n_eval} commits.  Cache: {cache}")
        print("\nRunning evaluation...")
        t0 = time.time()
        raw, stats = run_evaluation(test_hashes, train_hashes, data, actual_map, client, args.max_commits)
        stats["llm"] = dict(gp.LLM_STATS)
        print(f"\n  Completed in {time.time() - t0:.0f}s   LLM stats: {dict(gp.LLM_STATS)}")

    print("\nComputing metrics...")
    metrics, extra, recs = score(
        raw, dedupe=not args.no_dedupe, data=data, ground_truth=ground_truth,
        train_hashes=train_hashes, test_hashes=test_hashes, n_boot=args.bootstrap)
    for s, m in metrics.items():
        print(f"  {s:<18} {len(recs[s]):>5} eval points  macro-F1={m['macro']['f1']:.3f}  "
              f"micro-F1={m['micro']['f1']:.3f}")

    notes = []
    if stats.get("gt_points_in_full_radius") is not None:
        notes.append(f"Blast-radius cap: {stats['gt_points_in_capped_radius']} of "
                     f"{stats['gt_points_in_full_radius']} (commit, node) ground-truth points are inside "
                     f"the {gp.MAX_BLAST_NODES}-node cap; the rest are never scored. "
                     f"{stats.get('truncated_radii', 0)} radii were truncated.")
    notes.append(f"gt_mode={args.gt_mode}; dedupe={'on' if not args.no_dedupe else 'off'}; "
                 f"commits contributing points: {len({r['commit'] for r in recs.get('graphrag', [])})} "
                 f"of {len(test_hashes)} test commits.")
    extra["notes"] = notes

    print("\n" + "=" * 60)
    table = print_results(metrics, extra)

    prefix = "reanalysis_" if args.reanalyze else ""
    with open(os.path.join(args.out_dir, prefix + "evaluation_results.json"), "w") as f:
        json.dump({"config": config, "stats": stats, "metrics": metrics,
                   "diagnostics": extra, "records": raw}, f, indent=2)
    with open(os.path.join(args.out_dir, prefix + "table3.txt"), "w", encoding="utf-8") as f:
        f.write(table)
    print(f"\nWrote {args.out_dir}/{prefix}evaluation_results.json and {prefix}table3.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
