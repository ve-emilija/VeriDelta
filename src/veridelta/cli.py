"""Command-line interface.

    veridelta build-graph SRC --out GRAPH_DIR        build the typed class graph of a UVM project
    veridelta blast-radius CLASS --graph GRAPH_DIR   Stage 1 only: downstream classes (no model, no key)
    veridelta analyze CLASS --graph GRAPH_DIR        Stages 1-3: predicted failure categories per class

A graph directory holds ``node_types.json`` and ``adjacency.json`` (written by ``build-graph``) and,
optionally, ``commits.json`` (the history used by Stage 2).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from collections import Counter, defaultdict

from . import __version__
from . import graphrag_pipeline as gp


class CliError(Exception):
    """A problem the user can fix (bad path, unknown class, missing key)."""


# ── helpers ───────────────────────────────────────────────────────────────────

def load_graph(graph_dir: str) -> tuple[dict, dict]:
    paths = {name: os.path.join(graph_dir, f"{name}.json") for name in ("node_types", "adjacency")}
    for p in paths.values():
        if not os.path.isfile(p):
            raise CliError(f"{p} not found. Build it with: veridelta build-graph SRC --out {graph_dir}")
    with open(paths["node_types"], encoding="utf-8") as f:
        node_types = json.load(f)
    with open(paths["adjacency"], encoding="utf-8") as f:
        adjacency = json.load(f)
    return node_types, adjacency


def load_history(path: str | None, graph_dir: str) -> dict:
    path = path or os.path.join(graph_dir, "commits.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require_class(name: str, adjacency: dict) -> None:
    if name in adjacency:
        return
    close = difflib.get_close_matches(name, list(adjacency), n=5, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise CliError(f"class '{name}' is not in the graph.{hint}")


def table(rows: list[tuple], header: tuple) -> str:
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(header), line(tuple("-" * w for w in widths))] + [line(r) for r in rows])


# ── build-graph ───────────────────────────────────────────────────────────────

def cmd_build_graph(a) -> int:
    from . import type_annotator as ta
    if not os.path.isdir(a.src):
        raise CliError(f"source directory not found: {a.src}")
    files = ta.collect_files(a.src, a.scan_all)
    if not files:
        raise CliError(f"no SystemVerilog source files found under {a.src} "
                       f"(use --scan-all to parse every .sv/.svh file)")
    try:
        G = ta.build_graph(files)
    except RuntimeError as e:                       # networkx missing
        raise CliError(str(e))
    ta.annotate_graph(G, inherit=a.inherit_types)
    os.makedirs(a.out, exist_ok=True)
    node_types = {n: {"type": G.nodes[n]["type"]} for n in G}
    adjacency = {n: sorted(G.successors(n)) for n in G}
    with open(os.path.join(a.out, "node_types.json"), "w", encoding="utf-8") as f:
        json.dump(node_types, f, indent=0)
    with open(os.path.join(a.out, "adjacency.json"), "w", encoding="utf-8") as f:
        json.dump(adjacency, f, indent=0)
    counts = Counter(v["type"] for v in node_types.values())
    print(f"{len(files)} files -> {G.number_of_nodes()} classes, {G.number_of_edges()} inheritance edges")
    print("types: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items(), key=lambda x: -x[1])))
    print(f"written to {a.out}/node_types.json and {a.out}/adjacency.json")
    return 0


# ── blast-radius ──────────────────────────────────────────────────────────────

def cmd_blast_radius(a) -> int:
    node_types, adjacency = load_graph(a.graph)
    require_class(a.cls, adjacency)
    radius, meta = gp.get_blast_radius(a.cls, adjacency, max_nodes=a.max_nodes, return_meta=True)
    stage1 = gp.build_stage1_context(a.cls, radius, node_types, [])
    if a.json:
        print(json.dumps({"changed_class": a.cls, "changed_type": stage1["changed_type"],
                          "full_size": meta["full_size"], "truncated": meta["truncated"],
                          "blast_radius": stage1["blast_radius"]}, indent=2))
        return 0
    cap = f" (showing the nearest {a.max_nodes} of {meta['full_size']})" if meta["truncated"] else ""
    print(f"{a.cls}  type={stage1['changed_type']}  ->  {len(radius)} downstream classes{cap}")
    if radius:
        rows = [(it["depth"], it["node"], it["type"], it["primary"] or "-") for it in stage1["blast_radius"]]
        print(table(rows, ("depth", "class", "type", "structural prior")))
    return 0


# ── analyze ───────────────────────────────────────────────────────────────────

def cmd_analyze(a) -> int:
    node_types, adjacency = load_graph(a.graph)
    require_class(a.cls, adjacency)
    history = load_history(a.history, a.graph)
    if a.mock:
        from .mock import MockClient
        client = MockClient()
        gp.configure_llm(a.cache_dir, a.temperature)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise CliError("ANTHROPIC_API_KEY is not set. Export it, or use --mock to try the pipeline "
                           "offline, or run 'veridelta blast-radius' for the structural part only.")
        if a.model:
            gp.MODEL = a.model
        gp.configure_llm(a.cache_dir, a.temperature)
        client = gp.make_client()
    data = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": history}
    result = gp.predict(a.cls, a.ip, data, client, verbose=a.verbose, exclude_commits=tuple(a.exclude or ()))
    if a.json:
        print(json.dumps(result, indent=2))
        return 0
    n = len(result["blast_radius"])
    note = "" if history else "  (no history file: Stage 2 skipped)"
    print(f"{a.cls}: {n} downstream classes, {result['few_shot_commits_used']} similar commits used{note}")
    by_cat = defaultdict(list)
    for node, pred in result["predictions"].items():
        for cat in pred["categories"]:
            by_cat[cat].append((node, pred["confidence"], pred["node_type"], pred.get("source", "")))
    for cat in gp.VALID_CATEGORIES:
        items = sorted(by_cat.get(cat, []), key=lambda x: (-x[1], x[0]))
        if items:
            print(f"\n{cat} ({len(items)})")
            print(table([(i[0], i[2], f"{i[1]:.2f}", i[3]) for i in items[:a.top]],
                         ("class", "type", "conf", "source")))
            if len(items) > a.top:
                print(f"... and {len(items) - a.top} more (use --top or --json)")
    return 0


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="veridelta", description="Change impact analysis for UVM verification environments.")
    p.add_argument("--version", action="version", version=f"veridelta {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-graph", help="build the typed class graph of a UVM project")
    b.add_argument("src", help="directory with the SystemVerilog sources (for OpenTitan: the hw/ folder)")
    b.add_argument("--out", required=True, help="graph directory to write")
    b.add_argument("--scan-all", action="store_true", help="parse every .sv/.svh file, not only UVM-named ones")
    b.add_argument("--inherit-types", action="store_true", help="give untyped classes the type of their nearest typed ancestor")
    b.set_defaults(func=cmd_build_graph)

    r = sub.add_parser("blast-radius", help="downstream classes of a changed class (no model, no API key)")
    r.add_argument("cls", metavar="CLASS", help="name of the changed UVM class")
    r.add_argument("--graph", required=True, help="graph directory")
    r.add_argument("--max-nodes", type=int, default=gp.MAX_BLAST_NODES, help="cap on listed classes (default %(default)s)")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_blast_radius)

    n = sub.add_parser("analyze", help="predict failure categories for every downstream class")
    n.add_argument("cls", metavar="CLASS", help="name of the changed UVM class")
    n.add_argument("--graph", required=True, help="graph directory")
    n.add_argument("--ip", default="unknown", help="IP block of the change; used to rank similar commits")
    n.add_argument("--history", help="commit history JSON (default: GRAPH/commits.json if present)")
    n.add_argument("--exclude", nargs="*", metavar="HASH", help="commit hash prefixes to hide from retrieval")
    n.add_argument("--model", help=f"model id (default {gp.MODEL})")
    n.add_argument("--temperature", type=float, default=0.0)
    n.add_argument("--cache-dir", help="cache model replies on disk (reruns are free and identical)")
    n.add_argument("--mock", action="store_true", help="use a fake model (offline, free): answers the structural prior")
    n.add_argument("--top", type=int, default=10, help="rows listed per category (default %(default)s)")
    n.add_argument("--verbose", action="store_true")
    n.add_argument("--json", action="store_true")
    n.set_defaults(func=cmd_analyze)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as e:
        print(f"veridelta: error: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:                       # e.g. piped into `head`
        return 0


if __name__ == "__main__":
    sys.exit(main())
