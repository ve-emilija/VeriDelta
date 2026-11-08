"""
VeriDelta — GraphRAG Pipeline
graphrag_pipeline.py

Three-stage pipeline for category-aware blast radius prediction.

STAGE 1 — Structural subgraph retrieval
  Given a changed UVM component name, traverse the typed dependency graph
  (graph_edges.json) to identify all downstream nodes (blast radius).
  Each node is annotated with its type τ and primary DV failure category
  from node_types.json. Assertion Orphaning is handled separately via
  bind_index.json when the changed file is an RTL module.

STAGE 2 — Historical commit retrieval
  From commit_index.json (602 labeled commits), retrieve the top-k most
  similar historical commits using a scoring function over:
    - ip_block match           (+3)
    - changed node exact match (+3)
    - same node type match     (+2, once)
    - primary category in the commit's categories (+1, once)
  These form the few-shot examples in the LLM prompt.

STAGE 3 — LLM category inference
  Claude Haiku receives the typed blast radius + few-shot examples and
  outputs per-node category predictions with confidence scores as JSON.
  Model: claude-haiku-4-5-20251001
  (same model validated at κ=0.673 in the companion empirical study)

INPUTS  (flat layout, or scripts/ + data/ layout — see default_dirs())
  graph_edges.json   — adjacency list (type_annotator.py)
  node_types.json    — typed nodes   (type_annotator.py)
  bind_index.json    — RTL→bind map  (type_annotator.py)
  commit_index.json  — 602 commits   (ground_truth_mapper.py)

CHANGES vs. the version used for the submitted numbers
───────────────────────────────────────────────────────────────
* The blast-radius cap really is MAX_BLAST_NODES descendants (the old loop
  counted the changed node and returned 59).  Truncation is now deterministic
  — nearest depth first, alphabetical inside a depth — and `get_blast_radius`
  can report how large the uncapped radius was.
* `*_cov_bind` files hit by an RTL change are routed to Coverage Regression,
  not Assertion Orphaning (consistent with the τ rules: COV before SVA).
* predict() can exclude commits from retrieval (the demo used to retrieve the
  very commit it was demonstrating).
* LLM layer: lazy `anthropic` import, retry with backoff on 429/5xx, optional
  temperature, optional on-disk response cache (reruns are free and exactly
  reproducible), truncated-output salvage, safe confidence parsing, and
  counters (LLM_STATS) so silent fallbacks to the structural prior are visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL           = "claude-haiku-4-5-20251001"
MAX_FEW_SHOT    = 5      # top-k similar commits for few-shot context
MAX_BLAST_NODES = 60     # cap on DESCENDANTS in the prompt (changed node excluded)

VALID_CATEGORIES = [
    "Interface Drift",
    "Sequence Invalidation",
    "Checker Desync",
    "RAL Desync",
    "Coverage Regression",
]

# Primary category per node type — structural prior before LLM inference
TYPE_PRIMARY_CATEGORY = {
    "CFG":   "Interface Drift",
    "ENV":   "Interface Drift",
    "AGENT": "Interface Drift",
    "SEQ":   "Sequence Invalidation",
    "SCBD":  "Checker Desync",
    "RAL":   "RAL Desync",
    "COV":   "Coverage Regression",
    "SVA":   None,          # Assertion Orphaning is not predicted (future work)
    "OTHER": None,
}

STRUCTURAL_FALLBACK_CONF = 0.40


# ── Paths / data loading ──────────────────────────────────────────────────────

def default_dirs() -> tuple[str, str]:
    """
    (scripts_dir, data_dir).  If the JSON files sit next to this script (flat
    layout) both are the script directory; otherwise use the paper's
    scripts/ + ../data/ layout.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.exists(os.path.join(here, "graph_edges.json")):
        return here, here
    return here, os.path.join(os.path.dirname(here), "data")


def load_data(scripts_dir: str, data_dir: str) -> dict:
    """
    Load all four JSON files needed by the pipeline.
    Returns a single dict with keys: adjacency, node_types, bind_index, commit_index.
    """
    paths = {
        "adjacency":    os.path.join(scripts_dir, "graph_edges.json"),
        "node_types":   os.path.join(scripts_dir, "node_types.json"),
        "bind_index":   os.path.join(scripts_dir, "bind_index.json"),
        "commit_index": os.path.join(data_dir,    "commit_index.json"),
    }
    data = {}
    for key, path in paths.items():
        if not os.path.exists(path):
            print(f"Missing: {path}")
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            data[key] = json.load(f)
    return data


# ── Stage 1: Structural subgraph retrieval ────────────────────────────────────

def get_blast_radius(
    changed_node: str,
    adjacency:    dict[str, list[str]],
    max_nodes:    int = MAX_BLAST_NODES,
    return_meta:  bool = False,
):
    """
    BFS traversal of the dependency graph from changed_node.

    Returns [(node_name, depth), ...] sorted by depth then name — the changed
    node itself is excluded.  Depth = number of inheritance hops.  At most
    `max_nodes` descendants are returned; when the full radius is larger the
    nearest (then alphabetically first) ones are kept.

    With return_meta=True returns (radius, {"full_size": int, "truncated": bool}).
    NOTE: anything beyond the cap is invisible to the LLM *and* to the
    evaluation (a ground-truth node cut by the cap is never scored as a
    false negative) — evaluation.py reports how often this happens.
    """
    if changed_node not in adjacency:
        return ([], {"full_size": 0, "truncated": False}) if return_meta else []

    seen     = {changed_node}
    frontier = [changed_node]
    full: list[tuple[str, int]] = []
    depth = 0
    while frontier:
        depth += 1
        level = sorted({c for n in frontier for c in adjacency.get(n, []) if c not in seen})
        seen.update(level)
        full.extend((c, depth) for c in level)
        frontier = level

    result = full[:max_nodes]
    if return_meta:
        return result, {"full_size": len(full), "truncated": len(full) > max_nodes}
    return result


def get_bind_at_risk(
    rtl_module_stem: str,
    bind_index:      dict[str, list[str]],
) -> list[str]:
    """
    Given an RTL module name (stem of changed RTL file), return any
    bind files that reference it.
    """
    return bind_index.get(rtl_module_stem, [])


def bind_category(bind_file_stem: str) -> tuple[str, str]:
    """
    (τ type, failure category) for a bind file reached through the bind index.
    Coverage binds (…_cov_bind) attach covergroups, so a stale target breaks
    coverage collection, not assertions.  Mirrors the COV-before-SVA rule in
    type_annotator._RULES.
    """
    s = bind_file_stem.lower()
    if s.endswith("_cov_bind") or "cov_bind" in s or s.endswith("_cov_if"):
        return "COV", "Coverage Regression"
    return "SVA", "Assertion Orphaning"


def build_stage1_context(
    changed_node:    str,
    blast_radius:    list[tuple[str, int]],
    node_types:      dict,
    bind_at_risk:    list[str],
) -> dict:
    """
    Annotate each blast radius node with its type and primary category.
    Returns structured context dict for Stage 3 prompt construction.
    """
    annotated = []
    for node, depth in blast_radius:
        info     = node_types.get(node, {})
        ntype    = info.get("type", "OTHER")
        primary  = TYPE_PRIMARY_CATEGORY.get(ntype)
        annotated.append({
            "node":     node,
            "depth":    depth,
            "type":     ntype,
            "primary":  primary,
        })

    changed_info = node_types.get(changed_node, {})
    return {
        "changed_node":  changed_node,
        "changed_type":  changed_info.get("type", "OTHER"),
        "blast_radius":  annotated,
        "bind_at_risk":  bind_at_risk,
    }


# ── Stage 2: Historical commit retrieval ─────────────────────────────────────

def score_commit(
    commit:       dict,
    changed_node: str,
    node_type:    str,
    ip_block:     str,
    node_types:   dict,
) -> int:
    """
    Similarity of a historical commit to the current task (maximum 8):
      +3 same IP block
      +3 the changed class appears among the commit's changed classes
      +2 some changed class of the commit has the same component type (once)
    """
    score = 0
    if commit.get("ip_block") == ip_block:
        score += 3
    changed_nodes = commit.get("changed_nodes", [])
    if changed_node in changed_nodes:
        score += 3
    for n in changed_nodes:
        if node_types.get(n, {}).get("type") == node_type:
            score += 2
            break
    return score


def retrieve_similar_commits(
    changed_node:  str,
    node_type:     str,
    ip_block:      str,
    commit_index:  dict,
    node_types:    dict,
    k:             int = MAX_FEW_SHOT,
    before:        str | None = None,
    exclude:       set | frozenset = frozenset(),
) -> list[dict]:
    """
    Top-k most similar historical commits.  Only commits strictly older than
    `before` (ISO date string) are eligible, and commits in `exclude` (the
    evaluation commits) never are.  Ties are broken by recency.
    """
    scored = []
    for chash, commit in commit_index.items():
        if chash in exclude or not commit.get("changed_nodes"):
            continue
        if before is not None and commit.get("date", "") >= before:
            continue
        s_ = score_commit(commit, changed_node, node_type, ip_block, node_types)
        if s_ > 0:
            scored.append((s_, commit.get("date", ""), chash, commit))
    scored.sort(key=lambda x: x[1], reverse=True)      # most recent first
    scored.sort(key=lambda x: -x[0])                   # stable: score desc, then recency
    return [{"hash": h[:8], **c} for _, _, h, c in scored[:k]]


# ── Stage 3: LLM category inference ──────────────────────────────────────────

def build_prompt(
    stage1:          dict,
    similar_commits: list[dict],
    ip_block:        str,
    node_types:      dict,
    change_message:  str | None = None,
    change_summary:  str | None = None,
) -> str:
    """
    Stage 3 prompt (Table III): changed class, typed blast radius, historical
    examples (changed classes and commit message, NO failure categories), and
    the output specification.

    change_message  the commit subject/body (free text, as before).
    change_summary  NEW: a compact structural summary of what changed inside
                     the changed class itself — members/methods/constraints
                     added, removed or changed, and curated API-call deltas
                     (change_summary.render_change_summary()). Before this,
                     the model never saw what changed, only which class
                     changed and its blast radius; see
                     docs/change_summary_validation.md for what this does and
                     does not capture, and the measured effect once it has
                     been run through the evaluation harness.
    """
    changed_node = stage1["changed_node"]
    changed_type = stage1["changed_type"]
    changed_prim = TYPE_PRIMARY_CATEGORY.get(changed_type) or "none"
    blast_radius = stage1["blast_radius"]

    lines = [
        "You are VeriDelta, an assistant for UVM verification change impact analysis.",
        "A UVM class was changed. For every downstream class in the blast radius,",
        "predict which verification failure categories may manifest.",
        "",
        f"CHANGED CLASS: {changed_node}",
        f"  type={changed_type}  prior={changed_prim}  ip={ip_block}",
    ] + ([f"  change description: {change_message}"] if change_message else []) + (
        ["", "WHAT CHANGED IN THE CLASS ITSELF:", change_summary] if change_summary else []
    ) + [
        "",
        f"BLAST RADIUS ({len(blast_radius)} downstream classes):",
    ]
    for item in blast_radius:
        prim = item["primary"] or "none"
        lines.append(f"  depth={item['depth']}  {item['node']:<45} type={item['type']:<6} prior={prim}")

    if similar_commits:
        lines += ["", f"HISTORICAL EXAMPLES ({len(similar_commits)} similar past commits):"]
        for i, c in enumerate(similar_commits, 1):
            cl = ", ".join(c.get("changed_nodes", [])[:3]) + (", ..." if len(c.get("changed_nodes", [])) > 3 else "")
            lines.append(f"  [{c['hash']}] ip={c.get('ip_block', '?')}")
            lines.append(f"    Changed: {cl or '-'}")
            lines.append(f"    Message: {c.get('subject', '')}")

    lines += [
        "",
        "Valid categories: Interface Drift | Sequence Invalidation | Checker Desync |",
        "RAL Desync | Coverage Regression",
        "",
        "Return ONLY a JSON object, one entry per blast radius class, no explanation:",
        '{"<class>": {"categories": ["<category>", ...], "confidence": <0.0-1.0>}, ...}',
        "Use only the valid category names. A class may have more than one category.",
        "The prior is only a starting point: use the historical examples to adjust it.",
    ]
    return "\n".join(lines)


# ── LLM transport: retry, temperature, disk cache, counters ──────────────────

LLM_CACHE_DIR: str | None = os.environ.get("VERIDELTA_LLM_CACHE") or None
LLM_TEMPERATURE: float | None = (
    float(os.environ["VERIDELTA_TEMPERATURE"]) if os.environ.get("VERIDELTA_TEMPERATURE") else None
)
LLM_MAX_RETRIES = 5
LLM_STATS: Counter = Counter()      # calls, cache_hits, retries, truncated, parse_fail, salvaged


def configure_llm(cache_dir: str | None = None, temperature: float | None = None) -> None:
    """Set the disk cache directory and sampling temperature (None = API default)."""
    global LLM_CACHE_DIR, LLM_TEMPERATURE
    LLM_CACHE_DIR = cache_dir
    LLM_TEMPERATURE = temperature
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)


def make_client(api_key: str | None = None):
    try:
        import anthropic                  # lazy: offline analysis needs no SDK
        return anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    except ImportError:                   # stdlib fallback with the same call shape
        from . import minimal_client
        return minimal_client.Client(api_key)


def _cache_path(prompt: str, max_tokens: int) -> str | None:
    if not LLM_CACHE_DIR:
        return None
    key = json.dumps([MODEL, max_tokens, LLM_TEMPERATURE, prompt], ensure_ascii=False)
    return os.path.join(LLM_CACHE_DIR, hashlib.sha256(key.encode()).hexdigest() + ".json")


def _retryable(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None)
    if code in (408, 409, 429, 500, 502, 503, 504, 529):
        return True
    return any(s in type(exc).__name__ for s in ("Connection", "Timeout", "RateLimit"))


def llm_text(prompt: str, client, max_tokens: int = 4096) -> tuple[str, str]:
    """
    One Haiku call → (text, stop_reason).  Cached on disk when a cache dir is
    configured; transient API errors are retried with exponential backoff and
    anything else propagates (it is never silently turned into a prediction).
    """
    path = _cache_path(prompt, max_tokens)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        LLM_STATS["cache_hits"] += 1
        return d["text"], d.get("stop_reason", "")

    kwargs = dict(model=MODEL, max_tokens=max_tokens,
                  messages=[{"role": "user", "content": prompt}])
    if LLM_TEMPERATURE is not None:
        kwargs["temperature"] = LLM_TEMPERATURE

    for attempt in range(LLM_MAX_RETRIES):
        try:
            message = client.messages.create(**kwargs)
            break
        except TypeError as exc:
            if "temperature" in str(exc) and "temperature" in kwargs:
                kwargs.pop("temperature")          # newer SDKs reject it; the cache keeps reruns exact
                continue
            raise
        except Exception as exc:
            if "temperature" in str(exc) and "temperature" in kwargs and getattr(exc, "status_code", None) == 400:
                kwargs.pop("temperature")          # this model has deprecated the parameter
                LLM_STATS["temperature_dropped"] += 1
                continue
            if attempt == LLM_MAX_RETRIES - 1 or not _retryable(exc):
                raise
            LLM_STATS["retries"] += 1
            time.sleep(min(60, 2 ** attempt))

    LLM_STATS["calls"] += 1
    u = getattr(message, "usage", None)
    if u is not None:
        LLM_STATS[f"in_tokens[{MODEL}]"] += getattr(u, "input_tokens", 0) or 0
        LLM_STATS[f"out_tokens[{MODEL}]"] += getattr(u, "output_tokens", 0) or 0
    text = "".join(getattr(b, "text", "") or "" for b in (message.content or []))
    stop = getattr(message, "stop_reason", "") or ""
    if stop == "max_tokens":
        LLM_STATS["truncated"] += 1
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"text": text, "stop_reason": stop}, fh)
    return text, stop


_ENTRY = re.compile(r'"([^"\\]+)"\s*:\s*(\{[^{}]*\})')


def parse_llm_json(raw: str) -> tuple[dict, bool]:
    """
    Parse the model's JSON object.  Returns (obj, complete).  If the output is
    not valid JSON (typically truncated at max_tokens) the complete
    `"node": {...}` entries are salvaged and complete=False.
    """
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, True
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return obj, True
        except json.JSONDecodeError:
            pass
    salvaged = {}
    for name, body in _ENTRY.findall(raw):
        try:
            salvaged[name] = json.loads(body)
        except json.JSONDecodeError:
            continue
    return salvaged, False


def clean_predictions(parsed: dict) -> dict:
    """Validate categories against VALID_CATEGORIES and coerce confidence to [0,1]."""
    clean: dict = {}
    for node, data in parsed.items():
        if not isinstance(data, dict):
            continue
        cats = data.get("categories", [])
        if isinstance(cats, str):
            cats = [cats]
        if not isinstance(cats, list):
            continue
        cats = [c for c in cats if c in VALID_CATEGORIES]
        try:
            conf = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        if cats:
            clean[node] = {"categories": cats, "confidence": conf}
    return clean


def call_llm(prompt: str, client) -> dict:
    """
    Call Claude Haiku and parse the JSON response.

    Returns {node_name: {categories, confidence}}.  Unparseable output yields
    whatever complete entries could be salvaged (possibly {}); every such event
    is counted in LLM_STATS so the caller can see how often the structural
    prior silently replaced the LLM.
    """
    raw, _stop = llm_text(prompt, client, max_tokens=4096)
    parsed, complete = parse_llm_json(raw)
    if not complete:
        LLM_STATS["parse_fail" if not parsed else "salvaged"] += 1
    return clean_predictions(parsed)


def merge_predictions(stage1: dict, llm_output: dict, llm_source: str = "llm") -> dict:
    """
    Per blast-radius node: the LLM prediction if there is one, otherwise the
    structural prior at confidence 0.40 (source='structural_prior'); OTHER
    nodes the LLM skipped get no prediction.

    `depth` (inheritance hops from the changed node) is carried onto every
    prediction as a new, purely additive key — existing callers that read
    categories/confidence/node_type/source are unaffected; `predict()` turns
    it into a citeable `evidence` block (item 4: "each prediction cites the
    graph path ... it came from").
    """
    predictions: dict = {}
    for item in stage1["blast_radius"]:
        node, ntype, depth = item["node"], item["type"], item["depth"]
        if node in llm_output:
            predictions[node] = {**llm_output[node], "node_type": ntype, "source": llm_source, "depth": depth}
        elif item["primary"]:
            predictions[node] = {
                "categories": [item["primary"]],
                "confidence": STRUCTURAL_FALLBACK_CONF,
                "node_type":  ntype,
                "source":     "structural_prior",
                "depth":      depth,
            }
    return predictions


# ── Main prediction function ──────────────────────────────────────────────────

def predict(
    changed_node:    str,
    ip_block:        str,
    data:            dict,
    client,
    rtl_module:      str | None = None,
    verbose:         bool = False,
    exclude_commits: tuple[str, ...] | list[str] = (),
    change_message:  str | None = None,
    change_summary:  str | None = None,
) -> dict:
    """
    Run the full three-stage VeriDelta pipeline for a single changed node.

    Args:
        changed_node:    Name of the UVM class that was modified
        ip_block:        OpenTitan IP block name (e.g. 'uart', 'aes')
        data:            Loaded data dict from load_data()
        client:          Anthropic client instance
        rtl_module:      Optional RTL module name (stem of changed RTL file);
                         if given, bind_index is consulted
        verbose:         Print stage progress
        exclude_commits: hash prefixes removed from the retrieval pool (use this
                         when predicting a commit that is itself in commit_index)
        change_message:  commit subject/body, passed to build_prompt() verbatim
        change_summary:  structural change summary text (see build_prompt's
                         docstring and change_summary.py); typically built by
                         git_diff.render_for_commit() from a real git checkout
    """
    adjacency    = data["adjacency"]
    node_types   = data["node_types"]
    bind_index   = data["bind_index"]
    commit_index = data["commit_index"]
    if exclude_commits:
        ex = tuple(exclude_commits)
        commit_index = {h: c for h, c in commit_index.items() if not h.startswith(ex)}

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    if verbose:
        print(f"\n[Stage 1] Computing blast radius from '{changed_node}'...")

    blast_radius, meta = get_blast_radius(changed_node, adjacency, return_meta=True)
    bind_at_risk = get_bind_at_risk(rtl_module, bind_index) if rtl_module else []
    stage1       = build_stage1_context(
        changed_node, blast_radius, node_types, bind_at_risk
    )

    if verbose:
        cap = f" (capped from {meta['full_size']})" if meta["truncated"] else ""
        print(f"  Blast radius: {len(blast_radius)} nodes{cap}")
        if bind_at_risk:
            print(f"  Bind files at risk: {bind_at_risk}")

    if not blast_radius:
        if verbose:
            print(f"  '{changed_node}' not in graph or has no descendants.")
        return {
            "changed_node": changed_node,
            "ip_block":     ip_block,
            "blast_radius": [],
            "bind_at_risk": bind_at_risk,
            "predictions":  {},
            "model":        MODEL,
            "few_shot_commits_used": 0,
        }

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    changed_type = node_types.get(changed_node, {}).get("type", "OTHER")

    if verbose:
        print(f"\n[Stage 2] Retrieving similar commits (changed_type={changed_type})...")

    similar_commits = retrieve_similar_commits(
        changed_node, changed_type, ip_block, commit_index, node_types
    )

    if verbose:
        print(f"  Retrieved {len(similar_commits)} similar commits")
        for c in similar_commits:
            print(f"    {c['hash']} [{c.get('ip_block','?')}] "
                  f"→ {c.get('categories', [])}")

    # ── Stage 3 ───────────────────────────────────────────────────────────────
    if verbose:
        print(f"\n[Stage 3] Calling {MODEL}...")

    prompt = build_prompt(stage1, similar_commits, ip_block, node_types,
                           change_message=change_message, change_summary=change_summary)
    llm_output = call_llm(prompt, client)

    if verbose:
        print(f"  LLM returned predictions for {len(llm_output)} nodes")

    predictions = merge_predictions(stage1, llm_output)

    # Bind-level predictions (Stage 1 bind index)
    for bf in bind_at_risk:
        ntype, cat = bind_category(bf)
        predictions[bf] = {
            "categories": [cat],
            "confidence": 0.95,
            "node_type":  ntype,
            "source":     "bind_index",
            "depth":      None,
        }

    # Item 4 — evidence citations: every prediction names the graph path it
    # was reached by (inheritance depth, or the bind index) and the past
    # commits Stage 2 actually retrieved that mention this specific node (a
    # subset of `similar_commits` — most of those commits are about OTHER
    # blast-radius nodes and citing all of them on every node would be
    # noise, not evidence).
    commits_by_node: dict[str, list[str]] = {}
    for c in similar_commits:
        for n in c.get("changed_nodes", []):
            commits_by_node.setdefault(n, []).append(c["hash"])
    for node, pred in predictions.items():
        depth = pred.pop("depth", None)
        pred["evidence"] = {
            "graph_path": ("bind_index" if depth is None else f"inheritance, depth={depth}"),
            "cited_commits": commits_by_node.get(node, []),
        }

    return {
        "changed_node":          changed_node,
        "ip_block":              ip_block,
        "blast_radius":          [n for n, _ in blast_radius],
        "blast_radius_full_size": meta["full_size"],
        "bind_at_risk":          bind_at_risk,
        "predictions":           predictions,
        "model":                 MODEL,
        "few_shot_commits_used": len(similar_commits),
    }


# ── Standalone entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    d_scripts, d_data = default_dirs()
    ap = argparse.ArgumentParser(description="VeriDelta demo prediction")
    ap.add_argument("--scripts-dir", default=d_scripts)
    ap.add_argument("--data-dir",    default=d_data)
    ap.add_argument("--cache-dir",   default=LLM_CACHE_DIR)
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    print("=" * 60)
    print("VeriDelta  —  graphrag_pipeline.py")
    print("=" * 60)

    print("\nLoading data files...")
    data = load_data(args.scripts_dir, args.data_dir)
    print(f"  Graph nodes:      {len(data['node_types'])}")
    print(f"  Graph adjacency:  {sum(len(v) for v in data['adjacency'].values())} edges")
    print(f"  Bind index:       {len(data['bind_index'])} RTL modules")
    print(f"  Commit index:     {len(data['commit_index'])} commits")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\nError: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)
    configure_llm(args.cache_dir, args.temperature)
    client = make_client()
    print(f"\nModel: {MODEL}  temperature={LLM_TEMPERATURE}  cache={LLM_CACHE_DIR}")

    # Case study commit 5989ce15: dv_base_env_cfg changed to support templated
    # RAL types.  The commit is excluded from retrieval so the demo cannot see
    # its own label.
    print("\n" + "─" * 60)
    print("Demo: predicting blast radius for commit 5989ce15 (excluded from retrieval)")
    print("─" * 60)

    t_start = time.time()
    result = predict(
        changed_node    = "dv_base_env_cfg",
        ip_block        = "dv",
        data            = data,
        client          = client,
        verbose         = True,
        exclude_commits = ("5989ce15",),
    )
    print(f"\nRuntime: {time.time() - t_start:.1f} seconds")

    print("\n" + "─" * 60)
    print("PREDICTIONS")
    print("─" * 60)
    print(f"Blast radius: {len(result['blast_radius'])} nodes")
    print(f"Few-shot commits used: {result['few_shot_commits_used']}")
    srcs = Counter(p.get("source") for p in result["predictions"].values())
    print(f"Prediction sources: {dict(srcs)}   LLM stats: {dict(LLM_STATS)}\n")

    by_category: dict[str, list] = defaultdict(list)
    for node, pred in result["predictions"].items():
        for cat in pred["categories"]:
            by_category[cat].append((node, pred["confidence"], pred["node_type"]))

    for cat in VALID_CATEGORIES:
        nodes = by_category.get(cat, [])
        if nodes:
            print(f"{cat} ({len(nodes)} nodes):")
            for node, conf, ntype in sorted(nodes, key=lambda x: -x[1])[:8]:
                src = result["predictions"][node].get("source", "")
                print(f"  {node:<45} conf={conf:.2f}  type={ntype}  [{src}]")
            if len(nodes) > 8:
                print(f"  ... and {len(nodes)-8} more")
            print()

    out_path = os.path.join(args.data_dir, "pipeline_demo_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Full result saved → {out_path}")
